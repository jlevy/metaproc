"""Extractor: Codex CLI per-attempt JSONL.

Codex emits a different stream-json schema from claude-code-cli:

- ``type=thread.started``        — session boundary, carries ``thread_id``
- ``type=turn.started``           — turn boundary
- ``type=turn.completed``         — terminal usage event:
   ``{type, usage:{input_tokens, output_tokens, cached_input_tokens,
     reasoning_output_tokens}}``
- ``type=item.completed``         — per-tool event, with an embedded ``item``:
    - ``item.type=command_execution``: ``item.command``, ``item.aggregated_output``,
      ``item.exit_code`` (sometimes), ``item.status``
    - ``item.type=file_change``: ``item.changes[]`` with ``{path, kind}`` entries
    - ``item.type=agent_message``: ``item.text``
    - ``item.type=reasoning``: thinking output (treated as internal)
    - ``item.type=mcp_tool_call`` / ``item.type=web_search``: special tool subtypes
- ``type=compaction``             — internal log-compaction event

Pairing: every ``item.completed`` is self-contained (both invocation and
result inline). No matching ``item.started`` events are required.

File-edit rollup (C1a, ``plan-2026-05-26-multi-adapter-resource-rollup``):
codex emits one ``file_change`` per agent edit operation, and a single
``file_change`` item may carry multiple ``changes[]`` entries with
different paths. We unpack each path to its own logical event and then
collapse consecutive same-path events via
:func:`collapse_consecutive_file_edits`.

Path convention matches claude_agent.py:
``<run-dir>/.logs/tasks/<step>/<item>/<filename>.jsonl``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from metaproc.agent_errors import classify_agent_error
from metaproc.io import iter_artifact_paths, iter_jsonl_objects
from metaproc.io.gz_io import artifact_sidecar_path
from metaproc.trace.extractors.common import (
    attempt_cost_attrs,
    bash_tool_usage_attrs,
    collapse_consecutive_file_edits,
    tool_usage_classification,
)
from metaproc.trace.ids import compute_span_id
from metaproc.trace.schema import (
    SpanStatus,
    TraceEvent,
    bounded_attr_text,
    tool_usage_attrs,
)


class CodexAgentExtractor:
    """V1 extractor for Codex CLI per-attempt JSONL logs."""

    source = "codex-agent"

    def detect(self, run_dir: Path) -> bool:
        """Detect new codex stream-json (v0.130+) by schema sniff.

        Codex and claude both write to ``.logs/tasks/`` so we can't key
        on path alone; we sniff the first few lines of any task log for
        a ``turn.completed`` / ``item.completed`` event type. The
        sidecar's ``adapter_type`` is NOT enough — old codex versions
        emit a claude-shaped schema with ``adapter_type=codex-cli`` and
        are handled by :class:`ClaudeAgentExtractor`.
        """
        tasks_dir = run_dir / ".logs" / "tasks"
        if not tasks_dir.is_dir():
            return False
        return any(
            _looks_like_codex(jsonl_path)
            for jsonl_path in iter_artifact_paths(tasks_dir, "**/*.jsonl")
        )

    def extract(self, run_dir: Path, *, trace_id: str) -> Iterable[TraceEvent]:
        tasks_dir = run_dir / ".logs" / "tasks"
        if not tasks_dir.is_dir():
            return
        for jsonl_path in iter_artifact_paths(tasks_dir, "**/*.jsonl"):
            if not _is_codex_log(jsonl_path):
                continue
            yield from self._extract_one(jsonl_path, trace_id=trace_id, run_dir=run_dir)

    def _extract_one(
        self, jsonl_path: Path, *, trace_id: str, run_dir: Path
    ) -> Iterable[TraceEvent]:
        step_id, item_key = _parse_step_and_item_key(jsonl_path, run_dir=run_dir)
        events = list(iter_jsonl_objects(jsonl_path))
        if not events:
            return

        sidecar_path = artifact_sidecar_path(jsonl_path, ".jsonl.invocation.json")
        invocation = _load_sidecar(sidecar_path)

        ts_first = _first_timestamp(events)
        ts_last = _last_timestamp(events)
        turn_completed = next((e for e in events if e.get("type") == "turn.completed"), None)
        thread_started = next((e for e in events if e.get("type") == "thread.started"), None)

        attempt_span_id = compute_span_id(self.source, str(jsonl_path), "attempt")
        attempt_attrs: dict[str, Any] = {
            "step.id": step_id,
            "item.key": item_key,
            "attempt.jsonl_path": str(jsonl_path),
            "raw_log_ref": str(jsonl_path),
        }
        thread_id: str | None = None
        if isinstance(thread_started, dict):
            tid = thread_started.get("thread_id")
            if isinstance(tid, str):
                thread_id = tid
                attempt_attrs["attempt.session_id"] = tid

        if isinstance(turn_completed, dict):
            usage = turn_completed.get("usage")
            if isinstance(usage, dict):
                for raw_key, span_key in (
                    ("input_tokens", "attempt.tokens_input"),
                    ("output_tokens", "attempt.tokens_output"),
                    ("cached_input_tokens", "attempt.tokens_cached"),
                ):
                    val = usage.get(raw_key)
                    if isinstance(val, (int, float)):
                        attempt_attrs[span_key] = val

        _apply_sidecar_attrs(invocation, attempt_attrs)

        # Pre-P0.2 fallback: extract model from argv (codex -m <model>).
        if "adapter.model" not in attempt_attrs and invocation:
            model = _model_from_argv(invocation.get("argv"))
            if model:
                attempt_attrs["adapter.model"] = model
            if "adapter.provider" not in attempt_attrs:
                attempt_attrs["adapter.provider"] = "openai"

        # P1.6: canonical cost attrs (codex has no self-reported cost).
        attempt_attrs.update(
            attempt_cost_attrs(
                model=attempt_attrs.get("adapter.model"),
                provider=attempt_attrs.get("adapter.provider"),
                input_tokens=attempt_attrs.get("attempt.tokens_input"),
                output_tokens=attempt_attrs.get("attempt.tokens_output"),
                cached_tokens=attempt_attrs.get("attempt.tokens_cached"),
                self_reported_cost=None,
            )
        )

        status = _classify_attempt_status(events, turn_completed)
        error = _attempt_error_payload(events) if status == "error" else None
        yield TraceEvent(
            trace_id=trace_id,
            span_id=attempt_span_id,
            name=f"{step_id}/{item_key}",
            kind="attempt",
            source=self.source,
            ts_start=ts_first,
            ts_end=ts_last,
            duration_ms=None,
            status=status,
            status_derived=turn_completed is None,
            error=error,
            attributes=attempt_attrs,
        )

        # Session span (codex calls it "thread")
        session_span_id: str | None = None
        if thread_id:
            session_span_id = compute_span_id(self.source, str(jsonl_path), thread_id)
            yield TraceEvent(
                trace_id=trace_id,
                span_id=session_span_id,
                parent_span_id=attempt_span_id,
                name=f"agent_session/{thread_id[:8]}",
                kind="agent_session",
                source=self.source,
                ts_start=ts_first,
                ts_end=ts_last,
                status="ok" if turn_completed is not None else "unknown",
                attributes={
                    "step.id": step_id,
                    "item.key": item_key,
                    "agent.session_id": thread_id,
                },
            )

        # Per-tool spans (with C1a file-edit collapse).
        yield from self._tool_spans(
            events,
            trace_id=trace_id,
            parent_span_id=session_span_id or attempt_span_id,
            jsonl_path=jsonl_path,
            step_id=step_id,
            item_key=item_key,
        )

        # Internal spans (compaction).
        for idx, ev in enumerate(events):
            if ev.get("type") == "compaction":
                yield TraceEvent(
                    trace_id=trace_id,
                    span_id=compute_span_id(self.source, str(jsonl_path), "compaction", idx),
                    parent_span_id=session_span_id or attempt_span_id,
                    name="compaction",
                    kind="internal",
                    source=self.source,
                    ts_start="",
                    status="ok",
                    attributes={
                        "step.id": step_id,
                        "item.key": item_key,
                        "internal.kind": "compaction",
                    },
                )

    def _tool_spans(
        self,
        events: list[dict[str, Any]],
        *,
        trace_id: str,
        parent_span_id: str,
        jsonl_path: Path,
        step_id: str,
        item_key: str,
    ) -> Iterable[TraceEvent]:
        """Emit tool_call spans for item.completed events.

        - command_execution → tool.name='bash', tool.input.command set,
          arena classification via bash_tool_usage_attrs.
        - file_change → expanded per change path, then C1a collapse.
        - mcp_tool_call / web_search → through tool_usage_classification.
        - agent_message / reasoning → skipped (not tool calls).
        """
        # Step 1: flatten file_change events into per-path edit pseudo-events.
        flat: list[dict[str, Any]] = []
        for ev in events:
            if ev.get("type") != "item.completed":
                continue
            item = ev.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type") or item.get("item_type")
            if item_type == "file_change":
                # One pseudo-event per change path
                changes = item.get("changes")
                if isinstance(changes, list):
                    for change in changes:
                        if not isinstance(change, dict):
                            continue
                        path = change.get("path")
                        if not isinstance(path, str):
                            continue
                        flat.append(
                            {
                                "_codex_pseudo_type": "file_edit",
                                "_path": path,
                                "_kind": change.get("kind") or "edit",
                                "_item": item,
                            }
                        )
                else:
                    # Some codex versions write item.path directly
                    path = item.get("path")
                    if isinstance(path, str):
                        flat.append(
                            {
                                "_codex_pseudo_type": "file_edit",
                                "_path": path,
                                "_kind": item.get("kind") or "edit",
                                "_item": item,
                            }
                        )
            else:
                flat.append(ev)

        # Step 2: collapse consecutive same-path file edits.
        collapsed = collapse_consecutive_file_edits(
            flat,
            path_of=lambda e: e.get("_path") if isinstance(e.get("_path"), str) else None,
            is_file_edit=lambda e: e.get("_codex_pseudo_type") == "file_edit",
        )

        # Step 3: emit spans.
        idx = 0
        for entry in collapsed:
            ev = entry["event"]
            edit_count = entry.get("edit_count")
            if edit_count is not None:
                # Collapsed file-edit run.
                path = entry["path"]
                span_id = compute_span_id(self.source, str(jsonl_path), f"file_edit:{path}:{idx}")
                attrs: dict[str, Any] = {
                    "step.id": step_id,
                    "item.key": item_key,
                    "tool.use_id": f"file_edit_{idx}",
                    "tool.edit_count": edit_count,
                }
                attrs.update(
                    tool_usage_attrs(
                        tool__name="file_edit",
                        tool__family="file",
                        tool__operation="write",
                        tool__usage_role="artifact_io",
                        tool__input__path=bounded_attr_text(path, max_chars=1_000),
                        raw_log_ref=f"{jsonl_path}#file_edit:{idx}",
                    )
                )
                yield TraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    name="file_edit",
                    kind="tool_call",
                    source=self.source,
                    ts_start="",
                    status="ok",
                    attributes=attrs,
                )
                idx += 1
                continue

            # Regular item.completed event.
            if ev.get("type") != "item.completed":
                continue
            item = ev.get("item") or {}
            assert isinstance(item, dict)
            item_type = item.get("type") or item.get("item_type")
            if item_type in {"agent_message", "reasoning"}:
                continue  # not tool calls

            tool_name, attrs, name = _classify_codex_item(item_type, item, jsonl_path, idx)
            if tool_name is None:
                idx += 1
                continue

            common: dict[str, Any] = {
                "step.id": step_id,
                "item.key": item_key,
                "tool.use_id": str(item.get("id") or f"item_{idx}"),
            }
            common.update(attrs)
            yield TraceEvent(
                trace_id=trace_id,
                span_id=compute_span_id(self.source, str(jsonl_path), f"item_{idx}"),
                parent_span_id=parent_span_id,
                name=name,
                kind="tool_call",
                source=self.source,
                ts_start="",
                status=_codex_item_status(item),
                attributes=common,
            )
            idx += 1


def _classify_codex_item(
    item_type: str | None,
    item: dict[str, Any],
    jsonl_path: Path,
    idx: int,
) -> tuple[str | None, dict[str, Any], str]:
    """Return (tool_name, attrs, span_name) for one codex item.

    tool_name = None means "not a tool call" — caller should skip.
    """
    if item_type == "command_execution":
        command = item.get("command")
        if not isinstance(command, str):
            command = ""
        attrs = bash_tool_usage_attrs(command)
        attrs["tool.name"] = "bash"
        attrs["raw_log_ref"] = f"{jsonl_path}#item_{idx}"
        return "bash", attrs, "bash"
    if item_type == "mcp_tool_call":
        tool_name = str(item.get("tool_name") or item.get("name") or "mcp_call")
        tool_input = item.get("arguments")
        if not isinstance(tool_input, dict):
            tool_input = {}
        attrs = tool_usage_classification(tool_name, tool_input)
        attrs["tool.name"] = tool_name
        attrs["raw_log_ref"] = f"{jsonl_path}#item_{idx}"
        return tool_name, attrs, tool_name
    if item_type == "web_search":
        query = (
            item.get("query") or item.get("input", {}).get("query")
            if isinstance(item.get("input"), dict)
            else item.get("query")
        )
        attrs = tool_usage_attrs(
            tool__name="web_search",
            tool__family="web",
            tool__operation="search",
            tool__usage_role="research",
            tool__input__query=bounded_attr_text(query),
            source_origin="agent_web_search",
            raw_log_ref=f"{jsonl_path}#item_{idx}",
        )
        return "web_search", attrs, "web_search"
    return None, {}, ""


def _codex_item_status(item: dict[str, Any]) -> SpanStatus:
    status = item.get("status")
    exit_code = item.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return "error"
    if isinstance(status, str) and status.lower() in {"error", "failed"}:
        return "error"
    return "ok"


def _classify_attempt_status(
    events: list[dict[str, Any]], turn_completed: dict[str, Any] | None
) -> SpanStatus:
    if turn_completed is None:
        return "error"
    # Look for turn.failed or any error markers.
    if any(e.get("type") == "turn.failed" for e in events):
        return "error"
    return "ok"


def _attempt_error_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the attempt error dict from codex events. Codex doesn't have
    a single result-line equivalent, so we look for ``turn.failed`` or
    the last item with non-zero exit code.
    """
    for ev in events:
        if ev.get("type") == "turn.failed":
            message = ev.get("error") or ev.get("message")
            if isinstance(message, str):
                return {
                    "code": classify_agent_error(message),
                    "message": message[:500],
                }
            return {"code": "agent_error"}
    return {"code": "agent_error"}


def _parse_step_and_item_key(jsonl_path: Path, *, run_dir: Path) -> tuple[str, str]:
    try:
        rel = jsonl_path.relative_to(run_dir / ".logs" / "tasks")
    except ValueError:
        return ("unknown", "unknown")
    parts = rel.parts
    step = parts[0] if len(parts) >= 1 else "unknown"
    item_key = parts[1] if len(parts) >= 2 else "unknown"
    return step, item_key


def _load_sidecar(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _model_from_argv(argv: Any) -> str | None:
    """Extract model name from codex argv (``codex -m <model> ...``)."""
    if not isinstance(argv, list):
        return None
    for i, arg in enumerate(argv):
        if not isinstance(arg, str):
            continue
        if arg in ("-m", "--model") and i + 1 < len(argv):
            val = argv[i + 1]
            if isinstance(val, str) and val and not val.startswith("-"):
                return val
    return None


def _apply_sidecar_attrs(invocation: dict[str, Any], attempt_attrs: dict[str, Any]) -> None:
    """Copy P0.2 normalized adapter metadata onto attempt span attrs."""
    metadata = invocation.get("metadata") if invocation else None
    if not isinstance(metadata, dict):
        return
    for sidecar_key, span_key in (
        ("adapter_type", "adapter.type"),
        ("adapter_provider", "adapter.provider"),
        ("adapter_model", "adapter.model"),
        ("adapter_trace_source", "adapter.trace_source"),
        ("execution_profile", "execution_profile"),
        ("lane_id", "lane_id"),
    ):
        val = metadata.get(sidecar_key)
        if isinstance(val, str) and val:
            attempt_attrs[span_key] = val


def _adapter_type_from_sidecar(sidecar: Path) -> str | None:
    if not sidecar.exists():
        return None
    try:
        payload = json.loads(sidecar.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        v = metadata.get("adapter_type") or metadata.get("adapter")
        if isinstance(v, str):
            return v
    return None


def _looks_like_codex(jsonl_path: Path) -> bool:
    """Sniff first ~10 events for the codex turn/item signature."""
    try:
        for i, ev in enumerate(iter_jsonl_objects(jsonl_path)):
            if i > 10:
                break
            t = ev.get("type", "")
            if t in {"turn.started", "turn.completed", "thread.started"}:
                return True
            if t == "item.completed":
                return True
    except OSError:
        return False
    return False


def _is_codex_log(jsonl_path: Path) -> bool:
    """Decide whether this log belongs to the codex extractor (vs claude).

    The discriminator is the *schema*, not the sidecar — old codex versions
    (pre v0.130) emit a claude-shaped schema and are handled by
    :class:`ClaudeAgentExtractor` (which stamps ``source=codex-agent`` via
    the sidecar's ``adapter_type``). Only NEW codex schema
    (``turn.completed`` / ``item.completed`` events) belongs here.
    """
    return _looks_like_codex(jsonl_path)


def _first_timestamp(events: list[dict[str, Any]]) -> str:
    """Codex events don't have per-event timestamps; we synthesize from
    file mtime if needed elsewhere. For now return empty string and let
    duration_ms be derived from attempt boundaries.
    """
    for e in events:
        ts = e.get("timestamp")
        if isinstance(ts, str):
            return ts
    return ""


def _last_timestamp(events: list[dict[str, Any]]) -> str | None:
    for e in reversed(events):
        ts = e.get("timestamp")
        if isinstance(ts, str):
            return ts
    return None
