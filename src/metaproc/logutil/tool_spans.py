"""Pair adapter `tool_call` ↔ `tool_result` log records into typed spans.

Adapters today emit `tool_call` events when a tool starts and `tool_result`
events when it finishes (the Pi parser pairs `tool_execution_start` /
`tool_execution_end` records into those kinds; Claude / Gemini / Codex
emit `tool_call` and `tool_result` directly via their own JSONL shape).
The resource joiner needs the *paired* spans — duration, success/failure
classification, and a taxonomy `tool_path` — not the raw two-event
sequence. This module is the pure data-side pairing helper that the
resource joiner consumes.

Pairing strategy:

- Producer correlation IDs preserved on normalized `LogEvent` records are
  authoritative across Claude, Gemini, Pi, and Codex.
- Records without an ID receive a deterministic ordinal scoped to the
  source stream, adapter, and tool name; FIFO-by-name closes those spans.
- Unmatched starts (the session crashed mid-tool) yield a span with
  ``ended_at=None`` and ``failure_class="unmatched_start"`` so the
  resource report can flag them rather than silently dropping the
  evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from metaproc.logutil.parsing import LogEvent

WAITING_NETWORK_API_LATENCY = ("waiting", "network", "api_latency")
EXECUTION_TOOL_PREFIX = "execution > tool"


@dataclass(frozen=True)
class ToolSpan:
    """One paired tool invocation with timing and classification.

    All fields apart from ``tool_name`` are nullable so the same model
    can describe matched, half-matched, and orphan results.
    """

    tool_name: str
    adapter: str
    invocation_id: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_s: float | None
    is_error: bool
    failure_class: str | None
    tool_path: tuple[str, ...]
    raw_call: dict[str, Any] | None = None
    raw_result: dict[str, Any] | None = None


def pair_tool_events(events: list[LogEvent]) -> list[ToolSpan]:
    """Walk ``events`` (in arrival order) and return a list of `ToolSpan`s.

    Tool call/result pairing rules:

    1. Match by the adapter's normalized producer invocation ID when present.
    2. Otherwise, FIFO within tool name within adapter.
    3. Unmatched calls become orphan spans with
       ``failure_class="unmatched_start"``; unmatched results become
       orphan spans with ``failure_class="unmatched_result"`` so the
       attribution audit can surface them.
    """
    pending: dict[tuple[str, str], list[_PendingCall]] = {}
    by_invocation_id: dict[tuple[str, str], _PendingCall] = {}
    fallback_ordinals: dict[tuple[str, str], int] = {}
    orphan_result_ordinals: dict[tuple[str, str], int] = {}
    spans: list[ToolSpan] = []

    for ev in events:
        if ev.kind == "tool_call":
            tool_name = _extract_tool_name(ev)
            producer_id = _extract_invocation_id(ev)
            if producer_id is None:
                ordinal_key = (ev.adapter, tool_name)
                ordinal = fallback_ordinals.get(ordinal_key, 0) + 1
                fallback_ordinals[ordinal_key] = ordinal
                invocation_id = f"fallback:{ev.adapter}:{tool_name}:{ordinal}"
            else:
                invocation_id = producer_id
            call = _new_pending(
                ev,
                tool_name=tool_name,
                invocation_id=invocation_id,
                has_producer_id=producer_id is not None,
            )
            identity_key = (ev.adapter, invocation_id)
            if identity_key in by_invocation_id:
                raise ValueError(f"duplicate pending tool invocation identity {invocation_id!r}")
            by_invocation_id[identity_key] = call
            pending.setdefault((ev.adapter, call.tool_name), []).append(call)
            continue

        if ev.kind == "tool_result":
            tool_name = _extract_tool_name(ev)
            producer_id = _extract_invocation_id(ev)

            matched: _PendingCall | None = None
            if producer_id:
                matched = by_invocation_id.pop((ev.adapter, producer_id), None)
            if matched is not None:
                queue = pending.get((ev.adapter, matched.tool_name), [])
                if matched in queue:
                    queue.remove(matched)
            else:
                queue = pending.get((ev.adapter, tool_name), [])
                eligible = (
                    next((call for call in queue if not call.has_producer_id), None)
                    if producer_id is not None
                    else (queue[0] if queue else None)
                )
                if eligible is not None:
                    matched = eligible
                    queue.remove(matched)
                    by_invocation_id.pop((matched.adapter, matched.invocation_id), None)

            if producer_id is not None:
                invocation_id = producer_id
            elif matched is not None:
                invocation_id = matched.invocation_id
            else:
                ordinal_key = (ev.adapter, tool_name)
                ordinal = orphan_result_ordinals.get(ordinal_key, 0) + 1
                orphan_result_ordinals[ordinal_key] = ordinal
                invocation_id = f"fallback-result:{ev.adapter}:{tool_name}:{ordinal}"

            spans.append(
                _build_paired_span(
                    matched=matched,
                    result=ev,
                    invocation_id=invocation_id,
                )
            )

    # Anything still pending was started but never ended (session crash,
    # unflushed buffer, etc.). Surface them so the audit notices.
    for queue in pending.values():
        for call in queue:
            spans.append(_build_orphan_call(call))

    return spans


# ── Internals ──────────────────────────────────────────────────────


@dataclass
class _PendingCall:
    tool_name: str
    adapter: str
    started_at: datetime | None
    raw: dict[str, Any]
    invocation_id: str
    has_producer_id: bool
    summary: str = ""

    # Make the dataclass hashable on identity so we can safely .remove() it
    # from a list in the executionId fast path.
    __hash__ = object.__hash__  # type: ignore[assignment]


def _new_pending(
    ev: LogEvent,
    *,
    tool_name: str,
    invocation_id: str,
    has_producer_id: bool,
) -> _PendingCall:
    raw = ev.raw if isinstance(ev.raw, dict) else {}
    return _PendingCall(
        tool_name=tool_name,
        adapter=ev.adapter,
        started_at=_parse_ts(ev.timestamp),
        raw=raw,
        invocation_id=invocation_id,
        has_producer_id=has_producer_id,
        summary=ev.summary,
    )


def _build_paired_span(
    *, matched: _PendingCall | None, result: LogEvent, invocation_id: str
) -> ToolSpan:
    raw_result = result.raw if isinstance(result.raw, dict) else {}
    tool_name = matched.tool_name if matched is not None else _extract_tool_name(result)
    adapter = matched.adapter if matched is not None else result.adapter
    started_at = matched.started_at if matched is not None else None
    ended_at = _parse_ts(result.timestamp)
    duration_s = _duration(started_at, ended_at)

    failure_class: str | None
    if matched is None:
        failure_class = "unmatched_result"
    elif result.is_error:
        failure_class = "tool_error"
    else:
        failure_class = None

    return ToolSpan(
        tool_name=tool_name,
        adapter=adapter,
        invocation_id=invocation_id,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        is_error=bool(result.is_error),
        failure_class=failure_class,
        tool_path=_tool_path(adapter=adapter, tool_name=tool_name),
        raw_call=matched.raw if matched is not None else None,
        raw_result=raw_result,
    )


def _build_orphan_call(call: _PendingCall) -> ToolSpan:
    return ToolSpan(
        tool_name=call.tool_name,
        adapter=call.adapter,
        invocation_id=call.invocation_id,
        started_at=call.started_at,
        ended_at=None,
        duration_s=None,
        is_error=True,
        failure_class="unmatched_start",
        tool_path=_tool_path(adapter=call.adapter, tool_name=call.tool_name),
        raw_call=call.raw,
        raw_result=None,
    )


def _extract_tool_name(ev: LogEvent) -> str:
    if ev.tool_name:
        return ev.tool_name
    raw = ev.raw if isinstance(ev.raw, dict) else {}
    for key in ("toolName", "tool_name", "tool", "name"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    # Fall back to parsing the rendered summary, which is `[call:NAME] ...`
    # or `[result:NAME] ...` for every adapter the parsers in
    # `metaproc.logutil.parsing` produce today.
    return _tool_from_summary(ev.summary)


def _extract_invocation_id(ev: LogEvent) -> str | None:
    if ev.invocation_id:
        return ev.invocation_id
    raw = ev.raw if isinstance(ev.raw, dict) else {}
    for key in (
        "executionId",
        "toolCallId",
        "tool_use_id",
        "tool_id",
        "execution_id",
        "tool_call_id",
        "id",
    ):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _tool_from_summary(summary: str) -> str:
    """Pull NAME out of `[call:NAME]` / `[result:NAME]` / `[call:exec]` summaries."""
    if not summary or "[" not in summary or "]" not in summary:
        return "unknown"
    label = summary[summary.index("[") + 1 : summary.index("]")]
    if ":" in label:
        return label.split(":", 1)[1]
    return label or "unknown"


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _duration(started_at: datetime | None, ended_at: datetime | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    delta = (ended_at - started_at).total_seconds()
    if delta < 0:
        return None
    return delta


def _tool_path(*, adapter: str, tool_name: str) -> tuple[str, ...]:
    """Build the canonical taxonomy path for a tool span."""
    if not tool_name:
        tool_name = "unknown"
    family = _tool_family(adapter=adapter, tool_name=tool_name)
    return ("execution", "tool", family, tool_name)


_BUILTIN_FAMILIES: dict[str, str] = {
    "Bash": "bash",
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "Grep": "grep",
    "Glob": "glob",
    "TodoWrite": "todo",
    "WebFetch": "web",
    "WebSearch": "web",
}


def _tool_family(*, adapter: str, tool_name: str) -> str:
    """Classify a tool into a coarse family for the taxonomy path."""
    if tool_name in _BUILTIN_FAMILIES:
        return _BUILTIN_FAMILIES[tool_name]
    if tool_name.startswith("mcp__"):
        return "mcp"
    if adapter == "codex":
        return "codex"
    if adapter in ("claude", "claude-code"):
        return "claude"
    if adapter == "gemini":
        return "gemini"
    if adapter == "pi":
        return "pi"
    return "other"
