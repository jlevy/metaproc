"""Extractor: Pi CLI per-attempt JSONL.

Pi CLI wraps any underlying model (glm-5, gemini-flash, claude-opus, etc.)
and emits a camelCase stream-json schema:

- ``type=session``                — session header with ``version``, ``id``
- ``type=agent_start``            — agent boundary
- ``type=message_end``            — per-turn assistant/user messages with usage
- ``type=tool_execution_start``   — tool invocation, carries ``toolCallId``,
  ``toolName`` (camelCase), ``args``
- ``type=tool_execution_update``  — partial results (ignored for span purposes)
- ``type=tool_execution_end``     — tool result, carries ``toolCallId``,
  ``result``, ``isError``
- ``type=agent_end``              — terminal event with all messages and usage
- ``type=compaction``             — internal log-compaction event

Pairing: ``tool_execution_start`` and ``tool_execution_end`` are paired by
``toolCallId``. Unpaired starts (crashed attempt) emit a span with
``status=unknown``.

Token + cost extraction: ``agent_end.messages[]`` carries per-assistant-message
usage with ``{input, output, cacheRead, cacheWrite}`` and optionally
``cost.total``. We sum across all assistant messages. If ``cost.total`` is
present on any message, the sum is used as ``attempt.cost_usd``; pi reports it
from its own provider accounting, so it is recorded as a client-side estimate
unless a provider-authoritative figure is available.

Path convention matches claude_agent.py:
``<run-dir>/.logs/tasks/<step>/<item>/<filename>.jsonl``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from metaproc.adapters.billing import UNKNOWN
from metaproc.io import iter_artifact_paths, iter_jsonl_objects
from metaproc.io.gz_io import artifact_sidecar_path
from metaproc.trace.extractors.common import (
    attempt_cost_attrs,
    auth_route_from_invocation,
    tool_usage_classification,
)
from metaproc.trace.ids import compute_span_id
from metaproc.trace.schema import (
    SpanStatus,
    TraceEvent,
    tool_usage_attrs,
)


class PiAgentExtractor:
    """V1 extractor for Pi CLI per-attempt JSONL logs."""

    source = "pi-agent"

    def detect(self, run_dir: Path) -> bool:
        """Detect pi stream-json by schema sniff.

        Pi logs contain ``tool_execution_start`` / ``agent_end`` event
        types that no other adapter emits. We sniff the first few lines
        for these signatures.
        """
        tasks_dir = run_dir / ".logs" / "tasks"
        if not tasks_dir.is_dir():
            return False
        return any(
            _looks_like_pi(jsonl_path)
            for jsonl_path in iter_artifact_paths(tasks_dir, "**/*.jsonl")
        )

    def extract(self, run_dir: Path, *, trace_id: str) -> Iterable[TraceEvent]:
        tasks_dir = run_dir / ".logs" / "tasks"
        if not tasks_dir.is_dir():
            return
        for jsonl_path in iter_artifact_paths(tasks_dir, "**/*.jsonl"):
            if not _is_pi_log(jsonl_path):
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
        session_event = next((e for e in events if e.get("type") == "session"), None)
        agent_end = next((e for e in events if e.get("type") == "agent_end"), None)

        attempt_span_id = compute_span_id(self.source, str(jsonl_path), "attempt")
        attempt_attrs: dict[str, Any] = {
            "step.id": step_id,
            "item.key": item_key,
            "attempt.jsonl_path": str(jsonl_path),
            "raw_log_ref": str(jsonl_path),
        }

        # Session ID from session header.
        session_id: str | None = None
        if isinstance(session_event, dict):
            sid = session_event.get("id")
            if isinstance(sid, str):
                session_id = sid
                attempt_attrs["attempt.session_id"] = sid

        # Token + cost from agent_end.messages.
        model_from_messages: str | None = None
        if isinstance(agent_end, dict):
            messages = agent_end.get("messages")
            if isinstance(messages, list):
                usage_totals = _sum_assistant_usage(messages)
                for attr_key, total in (
                    ("attempt.tokens_input", usage_totals["input"]),
                    ("attempt.tokens_output", usage_totals["output"]),
                    ("attempt.tokens_cache_read", usage_totals["cache_read"]),
                    ("attempt.tokens_cache_write", usage_totals["cache_write"]),
                ):
                    if total is not None:
                        attempt_attrs[attr_key] = total
                if usage_totals["has_cost"]:
                    attempt_attrs["attempt.cost_usd"] = usage_totals["cost_total"]
                    attempt_attrs["attempt.cost_provenance"] = "client_list_estimate"
                    attempt_attrs["attempt.charge_status"] = UNKNOWN
                    attempt_attrs["attempt.cost_is_estimated"] = True
                model_from_messages = usage_totals["model"]

        _apply_sidecar_attrs(invocation, attempt_attrs)

        # pi delegates auth to whichever provider its config selects, so the
        # route is only sometimes provable from the environment; unknown is a
        # valid answer here.
        auth_route = auth_route_from_invocation(invocation, adapter_type="pi-cli")
        if auth_route:
            attempt_attrs["attempt.auth_route"] = auth_route

        # adapter.model fallback: if sidecar didn't provide it, use model
        # from agent_end messages.
        if "adapter.model" not in attempt_attrs and model_from_messages:
            attempt_attrs["adapter.model"] = model_from_messages

        # P1.6: pricing-table fallback when no self-reported cost.
        if "attempt.cost_usd" not in attempt_attrs:
            cost_attrs = attempt_cost_attrs(
                model=attempt_attrs.get("adapter.model"),
                provider=attempt_attrs.get("adapter.provider"),
                input_tokens=attempt_attrs.get("attempt.tokens_input"),
                output_tokens=attempt_attrs.get("attempt.tokens_output"),
                cached_tokens=attempt_attrs.get("attempt.tokens_cache_read"),
                self_reported_cost=None,
                auth_route=auth_route,
            )
            # Only take cost keys; token keys are already set above. Every cost
            # key must come across, or the attempt reaches the rollup with a
            # dollar figure and no provenance.
            if "attempt.cost_usd" in cost_attrs:
                for key in (
                    "attempt.cost_usd",
                    "attempt.cost_provenance",
                    "attempt.charge_status",
                    "attempt.cost_is_estimated",
                ):
                    if key in cost_attrs:
                        attempt_attrs[key] = cost_attrs[key]

        status = _classify_attempt_status(events, agent_end)
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
            status_derived=agent_end is None,
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
                status="ok" if agent_end is not None else "unknown",
                attributes={
                    "step.id": step_id,
                    "item.key": item_key,
                    "agent.session_id": session_id,
                },
            )

        parent_for_children = session_span_id or attempt_span_id

        # Per-tool spans: pair start/end by toolCallId.
        yield from self._tool_spans(
            events,
            trace_id=trace_id,
            parent_span_id=parent_for_children,
            jsonl_path=jsonl_path,
            step_id=step_id,
            item_key=item_key,
        )

        # Internal spans (compaction).
        for idx, ev in enumerate(events):
            if ev.get("type") == "compaction":
                ts = ev.get("timestamp") or ""
                yield TraceEvent(
                    trace_id=trace_id,
                    span_id=compute_span_id(self.source, str(jsonl_path), "compaction", idx),
                    parent_span_id=parent_for_children,
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
        """Pair ``tool_execution_start`` + ``tool_execution_end`` by ``toolCallId``.

        Start events are indexed; end events are consumed as they arrive.
        Unpaired starts at the end emit a span with ``status=unknown``.
        """
        # Collect starts indexed by toolCallId.
        pending: dict[str, dict[str, Any]] = {}
        paired: list[tuple[dict[str, Any], dict[str, Any]]] = []
        seen_order: list[str] = []

        for ev in events:
            ev_type = ev.get("type")
            if ev_type == "tool_execution_start":
                call_id = ev.get("toolCallId")
                if isinstance(call_id, str):
                    pending[call_id] = ev
                    seen_order.append(call_id)
            elif ev_type == "tool_execution_end":
                call_id = ev.get("toolCallId")
                if isinstance(call_id, str) and call_id in pending:
                    start_ev = pending.pop(call_id)
                    paired.append((start_ev, ev))

        # Emit paired spans.
        for idx, (start_ev, end_ev) in enumerate(paired):
            call_id = start_ev.get("toolCallId", f"tool_{idx}")
            tool_name = start_ev.get("toolName") or "unknown"
            args = start_ev.get("args")
            if not isinstance(args, dict):
                args = {}
            is_error = end_ev.get("isError", False)

            attrs: dict[str, Any] = {
                "step.id": step_id,
                "item.key": item_key,
                "tool.use_id": call_id,
            }

            # Classify through the shared helper (handles camelCase normalization).
            attrs["tool.name"] = tool_name
            classification = tool_usage_classification(tool_name, args)
            if classification:
                attrs.update(classification)
            else:
                # Fallback for unknown tool names: attach the raw input.
                attrs.update(
                    tool_usage_attrs(
                        tool__name=tool_name,
                        raw_log_ref=f"{jsonl_path}#tool:{call_id}",
                    )
                )
            attrs["raw_log_ref"] = f"{jsonl_path}#tool:{call_id}"

            status: SpanStatus = "error" if is_error else "ok"
            yield TraceEvent(
                trace_id=trace_id,
                span_id=compute_span_id(self.source, str(jsonl_path), f"tool:{call_id}"),
                parent_span_id=parent_span_id,
                name=str(tool_name),
                kind="tool_call",
                source=self.source,
                ts_start="",
                status=status,
                attributes=attrs,
            )

        # Emit unpaired starts (crashed attempt, no end event).
        for call_id in seen_order:
            if call_id not in pending:
                continue
            start_ev = pending[call_id]
            tool_name = start_ev.get("toolName") or "unknown"
            args = start_ev.get("args")
            if not isinstance(args, dict):
                args = {}
            attrs = {
                "step.id": step_id,
                "item.key": item_key,
                "tool.use_id": call_id,
                "tool.name": tool_name,
            }
            classification = tool_usage_classification(tool_name, args)
            if classification:
                attrs.update(classification)
            attrs["raw_log_ref"] = f"{jsonl_path}#tool:{call_id}"

            yield TraceEvent(
                trace_id=trace_id,
                span_id=compute_span_id(self.source, str(jsonl_path), f"tool:{call_id}"),
                parent_span_id=parent_span_id,
                name=str(tool_name),
                kind="tool_call",
                source=self.source,
                ts_start="",
                status="unknown",
                attributes=attrs,
            )


def _sum_assistant_usage(messages: list[Any]) -> dict[str, Any]:
    """Sum usage across all assistant messages in agent_end.

    Returns dict with keys: input, output, cache_read, cache_write,
    has_cost, cost_total, model.
    """
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    total_cost = 0.0
    has_cost = False
    has_any_usage = False
    model: str | None = None

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        # Capture model from first assistant message.
        if model is None:
            m = msg.get("model")
            if isinstance(m, str) and m:
                model = m
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        has_any_usage = True
        for raw_key, add_to in (
            ("input", "input"),
            ("output", "output"),
            ("cacheRead", "cache_read"),
            ("cacheWrite", "cache_write"),
        ):
            val = usage.get(raw_key)
            if isinstance(val, (int, float)):
                if add_to == "input":
                    total_input += val
                elif add_to == "output":
                    total_output += val
                elif add_to == "cache_read":
                    total_cache_read += val
                elif add_to == "cache_write":
                    total_cache_write += val
        cost = usage.get("cost")
        if isinstance(cost, dict) and "total" in cost:
            has_cost = True
            cost_val = cost.get("total")
            if isinstance(cost_val, (int, float)):
                total_cost += cost_val

    return {
        "input": total_input if has_any_usage else None,
        "output": total_output if has_any_usage else None,
        "cache_read": total_cache_read if has_any_usage else None,
        "cache_write": total_cache_write if has_any_usage else None,
        "has_cost": has_cost,
        "cost_total": total_cost,
        "model": model,
    }


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
    events: list[dict[str, Any]], agent_end: dict[str, Any] | None
) -> SpanStatus:
    if agent_end is None:
        return "error"
    return "ok"


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


def _looks_like_pi(jsonl_path: Path) -> bool:
    """Sniff first ~15 events for the pi signature event types."""
    try:
        for i, ev in enumerate(iter_jsonl_objects(jsonl_path)):
            if i > 15:
                break
            t = ev.get("type", "")
            if t in {"tool_execution_start", "tool_execution_end", "agent_end"}:
                return True
    except OSError:
        return False
    return False


def _is_pi_log(jsonl_path: Path) -> bool:
    """Decide whether this log belongs to the pi extractor."""
    return _looks_like_pi(jsonl_path)
