"""Post-run JSONL log compaction.

Strips redundant streaming events from CLI log files while preserving all
information needed for debugging, cost tracking, and the browser.

Runs on all adapter formats (Pi, Claude, Gemini) but is only non-trivial for
Pi CLI, which emits cumulative ``message_update`` events on every token.
Claude and Gemini handlers are no-op pass-throughs, ready for future rules.

Uses strif's ``atomic_output_file`` with backup for crash-safe file replacement.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from strif import atomic_output_file

from metaproc.io import ArtifactPath
from metaproc.logutil.parsing import detect_adapter

log = logging.getLogger(__name__)

# Events that the Pi compactor strips unconditionally.
# Note: message_update is handled specially (buffered for message_final),
# not listed here.
_PI_DROP_EVENTS = frozenset(
    {
        "message_start",
        "tool_execution_update",
        "turn_start",
        "turn_end",
    }
)

COMPACTION_TYPE = "compaction"
COMPACTION_VERSION = 1


@dataclass
class CompactionResult:
    """Summary of a compaction operation."""

    path: Path
    adapter: str
    original_size: int
    original_lines: int
    compacted_size: int
    compacted_lines: int
    already_compact: bool

    @property
    def lines_removed(self) -> int:
        return self.original_lines - self.compacted_lines

    @property
    def bytes_saved(self) -> int:
        return self.original_size - self.compacted_size

    @property
    def reduction_pct(self) -> float:
        if self.original_size == 0:
            return 0.0
        return 100.0 * self.bytes_saved / self.original_size


@dataclass(frozen=True)
class CompactionSweepSummary:
    """Summary of a recursive JSONL compaction sweep."""

    matched: int
    eligible: int
    compacted: int
    skipped: int
    failed: int
    original_bytes: int
    compacted_bytes: int
    saved_bytes: int


@dataclass(frozen=True)
class CompactionCandidate:
    """A JSONL file selected for compaction after size/adapter filters."""

    path: Path
    size: int
    adapter: str | None


@dataclass(frozen=True)
class CompactionPlan:
    """Dry-run-safe view of the files a compaction sweep would touch."""

    matched: int
    eligible: int
    skipped: int
    candidates: tuple[CompactionCandidate, ...]


def _walk_jsonl_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() == ".jsonl" else []
    if not root.is_dir():
        return []
    out: list[Path] = []
    for dirpath, _, filenames in os.walk(root, followlinks=False):
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() == ".jsonl" and not path.is_symlink():
                out.append(path)
    return sorted(out)


def _detect_file_adapter(path: Path) -> str:
    try:
        with ArtifactPath(path).open_text() as fh:
            return detect_adapter(
                [line.rstrip("\n") for _, line in zip(range(20), fh, strict=False)]
            )
    except OSError:
        return "unknown"


def plan_compaction(
    path: Path,
    *,
    min_size: int = 0,
    adapters: frozenset[str] | None = None,
    on_skipped: Callable[[Path, str], None] | None = None,
) -> CompactionPlan:
    """Return files that would be compacted using the real sweep filters."""
    files = _walk_jsonl_files(path)
    candidates: list[CompactionCandidate] = []
    skipped = 0
    for file_path in files:
        try:
            size = file_path.stat().st_size
        except OSError:
            skipped += 1
            if on_skipped is not None:
                on_skipped(file_path, "stat-failed")
            continue
        if size < min_size:
            skipped += 1
            if on_skipped is not None:
                on_skipped(file_path, "below-min-size")
            continue
        adapter: str | None = None
        if adapters is not None:
            adapter = _detect_file_adapter(file_path)
            if adapter not in adapters:
                skipped += 1
                if on_skipped is not None:
                    on_skipped(file_path, f"adapter={adapter}")
                continue
        candidates.append(CompactionCandidate(file_path, size, adapter))
    return CompactionPlan(
        matched=len(files),
        eligible=len(candidates),
        skipped=skipped,
        candidates=tuple(candidates),
    )


def compact_logs_path(
    path: Path,
    *,
    keep_original: bool = False,
    min_size: int = 0,
    adapters: frozenset[str] | None = None,
    on_compacted: Callable[[CompactionResult], None] | None = None,
    on_skipped: Callable[[Path, str], None] | None = None,
    on_failed: Callable[[Path, str], None] | None = None,
) -> CompactionSweepSummary:
    """Compact JSONL logs under ``path`` with optional adapter/size filters.

    ``adapters`` lets automated post-run cleanup target noisy adapter
    streams such as Pi and Codex without rewriting generic JSONL event
    files merely to add a compaction header. The ad hoc CLI keeps its
    broad legacy behavior by calling this with no adapter filter.
    """
    plan = plan_compaction(
        path,
        min_size=min_size,
        adapters=adapters,
        on_skipped=on_skipped,
    )
    skipped = plan.skipped

    compacted = 0
    failed = 0
    original_bytes = 0
    compacted_bytes = 0
    saved_bytes = 0
    for candidate in plan.candidates:
        file_path = candidate.path
        try:
            result = compact_log(file_path, keep_original=keep_original)
        except Exception as exc:  # noqa: BLE001 - best-effort sweep reports failures
            failed += 1
            if on_failed is not None:
                on_failed(file_path, str(exc))
            continue
        original_bytes += result.original_size
        compacted_bytes += result.compacted_size
        saved_bytes += result.bytes_saved
        if result.already_compact or result.lines_removed <= 0:
            skipped += 1
            if on_skipped is not None:
                reason = "already-compact" if result.already_compact else f"no-op-{result.adapter}"
                on_skipped(file_path, reason)
            continue
        compacted += 1
        if on_compacted is not None:
            on_compacted(result)

    return CompactionSweepSummary(
        matched=plan.matched,
        eligible=plan.eligible,
        compacted=compacted,
        skipped=skipped,
        failed=failed,
        original_bytes=original_bytes,
        compacted_bytes=compacted_bytes,
        saved_bytes=saved_bytes,
    )


def compact_log(path: Path, *, keep_original: bool = False) -> CompactionResult:
    """Compact a JSONL log file, removing redundant streaming events.

    Always runs regardless of adapter format.  For Claude and Gemini logs this
    is a no-op (the file is rewritten identically minus a compaction header).
    For Pi logs, cumulative ``message_update`` events are stripped.

    Gzipped inputs (``.jsonl.gz``) are treated as ``already_compact``: the
    post-step pipeline runs ``try_compact_log`` before ``gzip_text_path``,
    so a ``.gz`` here means both passes already happened. Returning early
    avoids the decompress + rewrite-as-plain round-trip that would
    *un*-compress the file.

    File replacement uses atomic moves with a backup:
    1. Write compacted output to a ``.partial`` temp file
    2. Move original to ``.bak`` and temp to final (via strif atomic_output_file)
    3. Remove ``.bak`` on success (unless ``keep_original`` is True)
    """
    path = Path(path)
    original_size = path.stat().st_size
    if path.suffix.lower() == ".gz":
        return CompactionResult(
            path=path,
            adapter="gzipped",
            original_size=original_size,
            original_lines=0,
            compacted_size=original_size,
            compacted_lines=0,
            already_compact=True,
        )
    raw_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    original_lines = len(raw_lines)

    # Detect if already compacted.
    if _is_already_compacted(raw_lines):
        log.debug("Already compacted: %s", path)
        return CompactionResult(
            path=path,
            adapter="unknown",
            original_size=original_size,
            original_lines=original_lines,
            compacted_size=original_size,
            compacted_lines=original_lines,
            already_compact=True,
        )

    # Detect adapter from first lines.
    first_lines = [line.rstrip("\n") for line in raw_lines[:20]]
    adapter = detect_adapter(first_lines)

    # Dispatch to adapter-specific compactor.
    if adapter == "pi":
        compacted = _compact_pi(raw_lines)
    elif adapter == "claude":
        compacted = _compact_claude(raw_lines)
    elif adapter == "gemini":
        compacted = _compact_gemini(raw_lines)
    elif adapter == "codex":
        compacted = _compact_codex(raw_lines)
    else:
        compacted = list(raw_lines)

    # Prepend compaction header.
    header = (
        json.dumps(
            {
                "type": COMPACTION_TYPE,
                "version": COMPACTION_VERSION,
                "adapter": adapter,
                "original_size": original_size,
                "original_lines": original_lines,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
        )
        + "\n"
    )
    compacted.insert(0, header)

    compacted_content = "".join(compacted)
    compacted_size = len(compacted_content.encode())
    compacted_lines = len(compacted)

    # Atomic write: temp -> backup original -> move temp into place.
    backup_suffix = ".bak"
    with atomic_output_file(path, backup_suffix=backup_suffix) as tmp_path:
        Path(tmp_path).write_text(compacted_content, encoding="utf-8")

    # Remove backup unless asked to keep it.
    backup_path = path.with_suffix(path.suffix + backup_suffix)
    if backup_path.exists() and not keep_original:
        backup_path.unlink()

    return CompactionResult(
        path=path,
        adapter=adapter,
        original_size=original_size,
        original_lines=original_lines,
        compacted_size=compacted_size,
        compacted_lines=compacted_lines,
        already_compact=False,
    )


def _is_already_compacted(lines: list[str]) -> bool:
    """Check if the file starts with a compaction header."""
    if not lines:
        return False
    try:
        first: Any = json.loads(lines[0])
        if not isinstance(first, dict):
            return False
        first_typed = cast(dict[str, Any], first)
        return first_typed.get("type") == COMPACTION_TYPE
    except (json.JSONDecodeError, ValueError):
        return False


def _compact_pi(lines: list[str]) -> list[str]:
    """Strip cumulative streaming events from Pi CLI logs.

    Drops: message_update, message_start, tool_execution_update,
    turn_start, turn_end.

    Keeps: session, agent_start, agent_end, message_end,
    tool_execution_start, tool_execution_end, auto_retry_start/end,
    and anything else unrecognized (safe default).

    For thinking preservation: buffers the last ``message_update`` before
    each ``message_end`` and emits it as ``message_final`` — but only when
    the ``message_end`` itself lacks thinking blocks (DeepSeek/GLM need it;
    Opus already has thinking in ``message_end``).
    """
    result: list[str] = []
    last_update_line: str | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            # Keep non-JSON lines (noise, stderr) unchanged.
            result.append(line)
            continue

        if not isinstance(event, dict):
            result.append(line)
            continue

        typed_event = cast(dict[str, Any], event)
        etype: str = typed_event.get("type", "")

        if etype == "message_update":
            # Buffer the latest update; only the last one before message_end matters.
            last_update_line = stripped
            continue

        if etype in _PI_DROP_EVENTS:
            continue

        if etype == "message_end":
            # Emit a message_final if the buffered update has thinking
            # and the message_end itself does NOT.
            if last_update_line is not None:
                _maybe_emit_final(last_update_line, typed_event, result)
            last_update_line = None

        result.append(line)

    return result


def _maybe_emit_final(update_json: str, end_event: dict[str, Any], result: list[str]) -> None:
    """Emit a ``message_final`` event from the last ``message_update`` if needed.

    Only emits when the update has thinking content but the ``message_end``
    does not — avoids duplication for models (Opus) whose ``message_end``
    already carries thinking.
    """
    # Check if message_end already has thinking.
    end_message: dict[str, Any] = end_event.get("message", {})
    end_content: list[Any] = end_message.get("content", [])
    if any(
        isinstance(b, dict) and cast(dict[str, Any], b).get("type") == "thinking"
        for b in end_content
    ):
        return

    # Parse the buffered update and look for thinking in its partial content.
    try:
        update = json.loads(update_json)
    except (json.JSONDecodeError, ValueError):
        return

    update_typed = cast(dict[str, Any], update)
    ame: dict[str, Any] = update_typed.get("assistantMessageEvent", {})
    partial: dict[str, Any] = ame.get("partial") or update_typed.get("partial") or {}
    content: list[Any] = partial.get("content", [])
    has_thinking = any(
        isinstance(b, dict) and cast(dict[str, Any], b).get("type") == "thinking" for b in content
    )
    if not has_thinking:
        return

    # Rename to message_final and emit.
    update_typed["type"] = "message_final"
    result.append(json.dumps(update_typed, separators=(",", ":")) + "\n")


# ── Live log anomaly detection ──────────────────────────────────

# Normal Pi CLI DeepSeek logs produce ~2 KB per output token.
# A Vertex SSE streaming anomaly can inflate this to ~12 MB/token (6000x).
# We check bytes/token after the file exceeds a size floor to avoid noise
# on small files where the ratio is meaningless.
LOG_RUNAWAY_SIZE_FLOOR = 100 * 1024 * 1024  # 100 MB — don't check below this
LOG_RUNAWAY_BYTES_PER_TOKEN = 500 * 1024  # 500 KB/token — normal is ~2 KB/token

# Error string used by the retry classifier (must match _TRANSIENT_PATTERNS in retry.py).
LOG_RUNAWAY_ERROR = "log_runaway: streaming anomaly detected"


@dataclass
class LogRunawayCheck:
    """Result of checking a live log for streaming anomaly."""

    file_bytes: int
    output_tokens: int
    bytes_per_token: float
    is_runaway: bool


def check_log_runaway(log_path: Path) -> LogRunawayCheck | None:
    """Check a live JSONL log for a streaming anomaly (e.g. Vertex SSE bug).

    Reads the file to count cumulative output tokens from ``message_end``
    events, then computes bytes-per-token.  Returns ``None`` if the file
    is below the size floor (too small to judge) or has no completed
    messages yet.

    This is designed to be called from the poll loop on in-progress logs.
    It is safe to call on a file that is being appended to concurrently.
    """
    try:
        file_bytes = log_path.stat().st_size
    except OSError:
        return None

    if file_bytes < LOG_RUNAWAY_SIZE_FLOOR:
        return None

    # Read the file and count output tokens from completed assistant messages.
    # We read the whole file rather than just the tail because message_end
    # events may be sparse (one per API round-trip) and we need the total.
    # ``ArtifactPath`` reads transparently from ``.jsonl`` or ``.jsonl.gz``.
    total_output_tokens = 0
    try:
        with ArtifactPath(log_path).open_text() as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                # Fast pre-filter: only parse lines that could be message_end
                if '"message_end"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") != "message_end":
                    continue
                msg = event.get("message", {})
                if msg.get("role") != "assistant":
                    continue
                usage = msg.get("usage") or {}
                total_output_tokens += usage.get("output", 0)
    except OSError:
        return None

    if total_output_tokens == 0:
        return None

    bytes_per_token = file_bytes / total_output_tokens
    is_runaway = bytes_per_token > LOG_RUNAWAY_BYTES_PER_TOKEN

    return LogRunawayCheck(
        file_bytes=file_bytes,
        output_tokens=total_output_tokens,
        bytes_per_token=bytes_per_token,
        is_runaway=is_runaway,
    )


def _compact_claude(lines: list[str]) -> list[str]:
    """Claude CLI compaction — currently a no-op pass-through."""
    return list(lines)


def _compact_gemini(lines: list[str]) -> list[str]:
    """Gemini CLI compaction — currently a no-op pass-through."""
    return list(lines)


# codex-cli 0.124.0 streaming-noise events that the compactor drops.
# item.completed + turn.completed / turn.failed carry the actual timeline and
# usage; the intermediate events below are redundant once the run is done.
_CODEX_DROP_EVENTS = frozenset(
    {
        "item.started",
        "item.updated",
    }
)


def _compact_codex(lines: list[str]) -> list[str]:
    """Strip codex-cli 0.124.0 streaming noise.

    Keeps: thread.started, turn.started, turn.completed, turn.failed,
    item.completed (the only item.* that carries the final payload).
    Drops: item.started, item.updated (streaming in-progress snapshots), and
    only known reconnect-noise ``error`` events.
    """
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            out.append(line)
            continue
        if not isinstance(event, dict):
            out.append(line)
            continue
        typed_event = cast(dict[str, Any], event)
        if typed_event.get("type") in _CODEX_DROP_EVENTS:
            continue
        if _is_codex_reconnect_error(typed_event):
            continue
        out.append(line)
    return out


def _is_codex_reconnect_error(event: dict[str, Any]) -> bool:
    """Return whether a Codex ``error`` event is known reconnect noise."""
    if event.get("type") != "error":
        return False
    text = " ".join(
        str(event.get(key) or "") for key in ("message", "error", "details", "reason")
    ).lower()
    return "reconnecting" in text and "codex" in text


def try_compact_log(log_path: Path) -> None:
    """Best-effort log compaction — never fails the run.

    Runs the streaming-noise compactor, then post-step-gzips the file if
    it meets the ``gzip-text`` per-extension size threshold. Both passes
    are best-effort and never raise into the orchestration path. Reads
    elsewhere in the codebase use ``ArtifactPath``/``iter_text_lines``
    and transparently handle the resulting ``.jsonl.gz``.
    """
    try:
        result = compact_log(log_path)
        if result.lines_removed > 0:
            log.debug(
                "Compacted %s: %d lines removed (%.0f%% reduction)",
                log_path.name,
                result.lines_removed,
                result.reduction_pct,
            )
    except Exception:
        log.warning("Log compaction failed for %s", log_path, exc_info=True)

    # Post-step gzip. Imported lazily to avoid a circular import with the
    # commands package at module import time.
    try:
        # Lazy import: top-level would create a cycle via
        # compaction → commands.gzip_text → cli → commands.compact_logs →
        # compaction (partially initialized).
        from metaproc.commands.gzip_text import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            gzip_text_path,
        )

        # exclude_active=False: the post-step caller knows the writer process
        # exited, so the file is sealed. Without this, the default 60s active
        # window silently skips every per-attempt log (mtime is brand new).
        gzip_text_path(log_path, exclude_active=False)
    except Exception:
        log.warning("Log gzip-text failed for %s", log_path, exc_info=True)
