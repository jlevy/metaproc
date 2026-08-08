"""Extractor: Gemini CLI per-attempt JSONL.

Gemini-cli (v0.42+) emits a flat stream-json schema:

- ``type=init``          — session boundary with ``session_id`` and ``model``
- ``type=message``       — user/assistant text turns
- ``type=tool_use``      — tool invocation with top-level ``tool_name``,
  ``tool_id``, and ``parameters``
- ``type=tool_result``   — tool completion with matching ``tool_id``,
  ``status`` (``'success'`` / ``'error'``), and ``output``
- ``type=compaction``    — internal log-compaction event
- ``type=result``        — terminal summary with ``status``, ``stats.models``
  token breakdowns, and aggregate ``stats.tool_calls``

Pairing: ``tool_use`` and ``tool_result`` are paired by the ``tool_id``
field (NOT FIFO). Gemini-cli's scheduler runs parallelizable tools via
``Promise.all`` and results return out of order. FIFO is only a fallback
for historical logs predating ``tool_id``; unpaired starts are marked
``status=unknown``.

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
from metaproc.logutil.usage import UsageStats, extract_gemini_usage
from metaproc.trace.extractors.common import (
    attempt_cost_attrs,
    span_duration_ms,
    tool_usage_classification,
)
from metaproc.trace.ids import compute_span_id
from metaproc.trace.schema import (
    SpanStatus,
    TraceEvent,
    tool_usage_attrs,
)


class GeminiAgentExtractor:
    """V1 extractor for Gemini CLI per-attempt JSONL logs."""

    source = "gemini-agent"

    def detect(self, run_dir: Path) -> bool:
        """Detect gemini stream-json by schema sniff.

        Gemini, claude, and codex all write to ``.logs/tasks/`` so we
        can't key on path alone; we sniff the first few lines for a
        ``tool_use`` event carrying a ``tool_id`` field (gemini's
        discriminator vs claude, which nests tool_use inside
        message.content blocks).
        """
        tasks_dir = run_dir / ".logs" / "tasks"
        if not tasks_dir.is_dir():
            return False
        return any(
            _looks_like_gemini(jsonl_path)
            for jsonl_path in iter_artifact_paths(tasks_dir, "**/*.jsonl")
        )

    def extract(self, run_dir: Path, *, trace_id: str) -> Iterable[TraceEvent]:
        tasks_dir = run_dir / ".logs" / "tasks"
        if not tasks_dir.is_dir():
            return
        for jsonl_path in iter_artifact_paths(tasks_dir, "**/*.jsonl"):
            if not _is_gemini_log(jsonl_path):
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
        result_event = next((e for e in events if e.get("type") == "result"), None)
        init_event = next((e for e in events if e.get("type") == "init"), None)

        attempt_span_id = compute_span_id(self.source, str(jsonl_path), "attempt")
        attempt_attrs: dict[str, Any] = {
            "step.id": step_id,
            "item.key": item_key,
            "attempt.jsonl_path": str(jsonl_path),
            "raw_log_ref": str(jsonl_path),
        }

        # Session ID from init event.
        session_id: str | None = None
        if isinstance(init_event, dict):
            sid = init_event.get("session_id")
            if isinstance(sid, str):
                session_id = sid
                attempt_attrs["attempt.session_id"] = sid
            model = init_event.get("model")
            if isinstance(model, str):
                attempt_attrs["adapter.model"] = model

        # Terminal stats from result event.
        if isinstance(result_event, dict):
            stats = result_event.get("stats")
            if isinstance(stats, dict):
                duration = stats.get("duration_ms")
                if isinstance(duration, (int, float)):
                    attempt_attrs["attempt.duration_ms"] = duration
                aggregate = UsageStats()
                for entry in extract_gemini_usage(stats):
                    aggregate += entry
                if aggregate.has_token_usage:
                    attempt_attrs["attempt.tokens_input"] = aggregate.input_tokens
                    attempt_attrs["attempt.tokens_output"] = aggregate.output_tokens
                    attempt_attrs["attempt.tokens_cached"] = aggregate.cache_read_tokens

        _apply_sidecar_attrs(invocation, attempt_attrs)

        # P1.6: canonical cost attrs (gemini has no self-reported cost).
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

        status = _classify_attempt_status(events, result_event)
        error = _attempt_error_payload(events) if status == "error" else None
        yield TraceEvent(
            trace_id=trace_id,
            span_id=attempt_span_id,
            name=f"{step_id}/{item_key}",
            kind="attempt",
            source=self.source,
            ts_start=ts_first,
            ts_end=ts_last,
            duration_ms=_duration_ms(result_event),
            status=status,
            status_derived=result_event is None,
            error=error,
            attributes=attempt_attrs,
        )

        # Session span.
        session_span_id: str | None = None
        if session_id:
            session_span_id = compute_span_id(self.source, str(jsonl_path), session_id)
            yield TraceEvent(
                trace_id=trace_id,
                span_id=session_span_id,
                parent_span_id=attempt_span_id,
                name=f"agent_session/{session_id[:8]}",
                kind="agent_session",
                source=self.source,
                ts_start=ts_first,
                ts_end=ts_last,
                status="ok" if result_event is not None else "unknown",
                attributes={
                    "step.id": step_id,
                    "item.key": item_key,
                    "agent.session_id": session_id,
                },
            )

        # Per-tool spans: pair tool_use + tool_result by tool_id.
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
                ts = ev.get("timestamp")
                yield TraceEvent(
                    trace_id=trace_id,
                    span_id=compute_span_id(self.source, str(jsonl_path), "compaction", idx),
                    parent_span_id=session_span_id or attempt_span_id,
                    name="compaction",
                    kind="internal",
                    source=self.source,
                    ts_start=ts if isinstance(ts, str) else "",
                    status="ok",
                    attributes={
                        "step.id": step_id,
                        "item.key": item_key,
                        "internal.kind": "compaction",
                        "internal.original_size": ev.get("original_size"),
                        "internal.original_lines": ev.get("original_lines"),
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
        """Pair tool_use + tool_result by tool_id and emit tool_call spans.

        Gemini-cli runs parallelizable tools via Promise.all, so results
        may return out of order. We pair by tool_id (NOT FIFO). tool_use
        events without a tool_id fall through to FIFO as a legacy fallback,
        and unpaired starts are marked status=unknown.
        """
        # Index: tool_id -> (tool_use event, timestamp)
        pending_by_id: dict[str, tuple[dict[str, Any], str]] = {}
        # FIFO fallback queue for events without tool_id
        fifo_queue: list[tuple[dict[str, Any], str]] = []
        tool_idx = 0

        for ev in events:
            ev_type = ev.get("type")
            ts = ev.get("timestamp")
            ts_str = ts if isinstance(ts, str) else ""

            if ev_type == "tool_use":
                tool_id = ev.get("tool_id")
                if isinstance(tool_id, str) and tool_id:
                    pending_by_id[tool_id] = (ev, ts_str)
                else:
                    fifo_queue.append((ev, ts_str))

            elif ev_type == "tool_result":
                tool_id = ev.get("tool_result_id") or ev.get("tool_id")
                if isinstance(tool_id, str) and tool_id and tool_id in pending_by_id:
                    use_ev, use_ts = pending_by_id.pop(tool_id)
                    yield _make_tool_span(
                        use_ev=use_ev,
                        use_ts=use_ts,
                        result_ev=ev,
                        result_ts=ts_str,
                        trace_id=trace_id,
                        source=self.source,
                        jsonl_path=jsonl_path,
                        parent_span_id=parent_span_id,
                        step_id=step_id,
                        item_key=item_key,
                        idx=tool_idx,
                    )
                    tool_idx += 1
                elif fifo_queue:
                    # FIFO fallback for legacy logs without tool_id
                    use_ev, use_ts = fifo_queue.pop(0)
                    yield _make_tool_span(
                        use_ev=use_ev,
                        use_ts=use_ts,
                        result_ev=ev,
                        result_ts=ts_str,
                        trace_id=trace_id,
                        source=self.source,
                        jsonl_path=jsonl_path,
                        parent_span_id=parent_span_id,
                        step_id=step_id,
                        item_key=item_key,
                        idx=tool_idx,
                    )
                    tool_idx += 1

        # Unpaired tool_use events (no matching result): status=unknown
        for use_ev, use_ts in pending_by_id.values():
            yield _make_tool_span(
                use_ev=use_ev,
                use_ts=use_ts,
                result_ev=None,
                result_ts=None,
                trace_id=trace_id,
                source=self.source,
                jsonl_path=jsonl_path,
                parent_span_id=parent_span_id,
                step_id=step_id,
                item_key=item_key,
                idx=tool_idx,
            )
            tool_idx += 1

        for use_ev, use_ts in fifo_queue:
            yield _make_tool_span(
                use_ev=use_ev,
                use_ts=use_ts,
                result_ev=None,
                result_ts=None,
                trace_id=trace_id,
                source=self.source,
                jsonl_path=jsonl_path,
                parent_span_id=parent_span_id,
                step_id=step_id,
                item_key=item_key,
                idx=tool_idx,
            )
            tool_idx += 1


def _make_tool_span(
    *,
    use_ev: dict[str, Any],
    use_ts: str,
    result_ev: dict[str, Any] | None,
    result_ts: str | None,
    trace_id: str,
    source: str,
    jsonl_path: Path,
    parent_span_id: str,
    step_id: str,
    item_key: str,
    idx: int,
) -> TraceEvent:
    """Build one tool_call span from a (tool_use, tool_result) pair."""
    tool_name = use_ev.get("tool_name") or "unknown"
    tool_id = use_ev.get("tool_id") or f"tool_{idx}"
    raw_parameters = use_ev.get("parameters")
    parameters: dict[str, Any] = raw_parameters if isinstance(raw_parameters, dict) else {}

    attrs: dict[str, Any] = {
        "step.id": step_id,
        "item.key": item_key,
        "tool.use_id": tool_id,
    }

    # Classify via the shared helper.
    attrs.update(
        tool_usage_attrs(
            tool__name=tool_name,
            raw_log_ref=f"{jsonl_path}#tool_use:{tool_id}",
        )
    )
    attrs.update(tool_usage_classification(str(tool_name), parameters))

    # Status from tool_result.
    status: SpanStatus
    error: dict[str, Any] | None
    if result_ev is None:
        status = "unknown"
        error = None
    else:
        result_status = result_ev.get("status")
        if isinstance(result_status, str) and result_status.lower() == "error":
            status = "error"
            error_payload = result_ev.get("error") or result_ev.get("output")
            error = {"kind": "tool_error"}
            if isinstance(error_payload, dict):
                msg = error_payload.get("message")
                if isinstance(msg, str):
                    error["message"] = msg[:500]
            elif isinstance(error_payload, str):
                error["message"] = error_payload[:500]
        else:
            status = "ok"
            error = None
            # Track result size when output is a string.
            output = result_ev.get("output")
            if isinstance(output, str):
                attrs["tool.result.size_bytes"] = len(output)

    span_id = compute_span_id(source, str(jsonl_path), tool_id)
    return TraceEvent(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=str(tool_name),
        kind="tool_call",
        source=source,
        ts_start=use_ts or result_ts or "",
        ts_end=result_ts,
        duration_ms=span_duration_ms(use_ts, result_ts),
        status=status,
        status_derived=False,
        error=error,
        attributes=attrs,
    )


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


def _classify_attempt_status(
    events: list[dict[str, Any]], result_event: dict[str, Any] | None
) -> SpanStatus:
    if result_event is None:
        return "error"
    result_status = result_event.get("status")
    if isinstance(result_status, str) and result_status.lower() == "error":
        return "error"
    return "ok"


def _attempt_error_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the attempt error dict from gemini events."""
    for ev in reversed(events):
        if ev.get("type") == "result":
            message = ev.get("error") or ev.get("message")
            if isinstance(message, str):
                return {
                    "code": classify_agent_error(message),
                    "message": message[:500],
                }
    return {"code": "agent_error"}


def _duration_ms(result_event: dict[str, Any] | None) -> float | None:
    if result_event is None:
        return None
    stats = result_event.get("stats")
    if isinstance(stats, dict):
        val = stats.get("duration_ms")
        return float(val) if isinstance(val, (int, float)) else None
    return None


def _first_timestamp(events: list[dict[str, Any]]) -> str:
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


def _looks_like_gemini(jsonl_path: Path) -> bool:
    """Sniff first ~15 events for the gemini signature: flat tool_use with tool_id."""
    try:
        for i, ev in enumerate(iter_jsonl_objects(jsonl_path)):
            if i > 15:
                break
            t = ev.get("type", "")
            # Gemini flat tool_use has top-level tool_id and tool_name.
            if t == "tool_use" and isinstance(ev.get("tool_id"), str):
                return True
            # The init event with a gemini model name is also a strong signal.
            if t == "init" and isinstance(ev.get("model"), str):
                model = ev["model"].lower()
                if "gemini" in model:
                    return True
    except OSError:
        return False
    return False


def _is_gemini_log(jsonl_path: Path) -> bool:
    """Decide whether this log belongs to the gemini extractor."""
    return _looks_like_gemini(jsonl_path)
