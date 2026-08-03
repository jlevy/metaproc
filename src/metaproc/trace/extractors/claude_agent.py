"""Extractor: Claude agent per-attempt JSONL.

Reads ``.logs/tasks/<step>/<item>/*.jsonl`` and emits:

- one ``attempt`` span per JSONL file (the parent of everything else from
  that file);
- one ``agent_session`` span per ``session_id`` seen in the file (typically
  one per file, but compaction can swap sessions);
- one ``tool_call`` span per matched ``tool_use`` → ``tool_result`` pair;
- ``internal`` spans for compaction, ``rate_limit_event``, and other
  non-tool events that carry operational signal.

Path convention: ``<run-dir>/.logs/tasks/<step>/<item>/<step>_<item>_<ts>.jsonl``.
The matching ``<...>.invocation.json`` sidecar (when present) carries the
adapter config (model, tools allowlist, env redacted). Both are tied to
the same ``attempt`` span.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from metaproc.agent_errors import classify_agent_error
from metaproc.io import iter_artifact_paths, iter_jsonl_objects
from metaproc.io.gz_io import artifact_sidecar_path
from metaproc.logutil.claude_usage import claude_attempt_usage
from metaproc.trace.extractors.codex_agent import _is_codex_log
from metaproc.trace.extractors.common import (
    attempt_cost_attrs,
    auth_route_from_invocation,
    tool_usage_classification,
)
from metaproc.trace.extractors.gemini_agent import _is_gemini_log
from metaproc.trace.extractors.pi_agent import _is_pi_log
from metaproc.trace.ids import compute_span_id
from metaproc.trace.schema import SpanStatus, TraceEvent, tool_usage_attrs


class ClaudeAgentExtractor:
    """V1 extractor for Claude per-attempt JSONL logs."""

    source = "claude-agent"

    def detect(self, run_dir: Path) -> bool:
        tasks_dir = run_dir / ".logs" / "tasks"
        if not tasks_dir.is_dir():
            return False
        return any(iter_artifact_paths(tasks_dir, "**/*.jsonl"))

    def extract(self, run_dir: Path, *, trace_id: str) -> Iterable[TraceEvent]:
        tasks_dir = run_dir / ".logs" / "tasks"
        if not tasks_dir.is_dir():
            return

        for jsonl_path in iter_artifact_paths(tasks_dir, "**/*.jsonl"):
            if _is_codex_log(jsonl_path) or _is_gemini_log(jsonl_path) or _is_pi_log(jsonl_path):
                continue
            yield from self._extract_one(jsonl_path, trace_id=trace_id, run_dir=run_dir)

    def _extract_one(
        self, jsonl_path: Path, *, trace_id: str, run_dir: Path
    ) -> Iterable[TraceEvent]:
        step_id, item_key = _parse_step_and_item_key(jsonl_path, run_dir=run_dir)
        events = list(_iter_jsonl(jsonl_path))
        if not events:
            return

        ts_first = _first_timestamp(events)
        ts_last = _last_timestamp(events)
        result_event = next((e for e in events if e.get("type") == "result"), None)

        invocation_json = artifact_sidecar_path(jsonl_path, ".jsonl.invocation.json")
        invocation = _load_invocation_sidecar(invocation_json)
        span_source = _source_from_invocation(invocation, default=self.source)

        attempt_span_id = compute_span_id(span_source, str(jsonl_path), "attempt")
        attempt_attrs: dict[str, Any] = {
            "step.id": step_id,
            "item.key": item_key,
            "attempt.jsonl_path": str(jsonl_path),
            "raw_log_ref": str(jsonl_path),
        }
        auth_route = auth_route_from_invocation(invocation, adapter_type="claude-code-cli")
        if result_event is not None:
            for key in ("total_cost_usd", "num_turns", "duration_ms", "session_id"):
                if key in result_event:
                    attempt_attrs[f"attempt.{key}"] = result_event[key]
            # P1.6: canonical cost + token attrs (cost-by-step preset finds these).
            tokens = _attempt_token_totals(result_event)
            attempt_attrs.update(
                attempt_cost_attrs(
                    model=None,  # multi-model attempt; per-model split is below
                    provider="anthropic",
                    input_tokens=tokens["input"],
                    output_tokens=tokens["output"],
                    cached_tokens=tokens["cache_read"],
                    self_reported_cost=result_event.get("total_cost_usd"),
                    auth_route=auth_route,
                )
            )
            if tokens["cache_write"] is not None:
                attempt_attrs["attempt.tokens_cache_write"] = tokens["cache_write"]
            if tokens["models"]:
                attempt_attrs["attempt.models"] = ",".join(tokens["models"])
        elif auth_route:
            attempt_attrs["attempt.auth_route"] = auth_route

        if invocation:
            for key in ("model", "effort", "tools", "permission_mode"):
                if key in invocation:
                    attempt_attrs[f"adapter.{key}"] = invocation[key]
            metadata = invocation.get("metadata")
            if isinstance(metadata, dict):
                adapter = metadata.get("adapter") or metadata.get("adapter_type")
                if isinstance(adapter, str) and adapter:
                    attempt_attrs["adapter.type"] = adapter
                # P0.2 adds canonical adapter metadata to the
                # sidecar so we don't have to infer from argv[0] or env vars.
                # Read whichever keys are present; sidecars from older runs
                # may not have them.
                for sidecar_key, span_key in (
                    ("adapter_provider", "adapter.provider"),
                    ("adapter_model", "adapter.model"),
                    ("adapter_trace_source", "adapter.trace_source"),
                    ("execution_profile", "execution_profile"),
                    ("lane_id", "lane_id"),
                ):
                    val = metadata.get(sidecar_key)
                    if isinstance(val, str) and val:
                        attempt_attrs[span_key] = val
            env = invocation.get("env_redacted")
            if isinstance(env, dict):
                # Env-var fallbacks for sidecars that pre-date P0.2.
                if "execution_profile" not in attempt_attrs:
                    execution_profile = env.get("EXECUTION_PROFILE")
                    if isinstance(execution_profile, str) and execution_profile:
                        attempt_attrs["execution_profile"] = execution_profile
                artifact_namespace = env.get("ARTIFACT_NAMESPACE")
                if isinstance(artifact_namespace, str) and artifact_namespace:
                    attempt_attrs["artifact_namespace"] = artifact_namespace
                # METAPROC_LANE_ID is injected by run_parallel for every
                # subprocess (degenerate runs see the profile name). Carrying
                # it on the attempt span lets lane-comparison consumers join
                # cleanly against consumer tool spans whose own
                # extractors already read this key.
                if "lane_id" not in attempt_attrs:
                    lane_id = env.get("METAPROC_LANE_ID") or env.get("LANE_ID")
                    if isinstance(lane_id, str) and lane_id:
                        attempt_attrs["lane_id"] = lane_id

        attempt_status = _classify_attempt_status(result_event, events)
        attempt_error = _attempt_error_payload(result_event) if attempt_status == "error" else None
        yield TraceEvent(
            trace_id=trace_id,
            span_id=attempt_span_id,
            name=f"{step_id}/{item_key}",
            kind="attempt",
            source=span_source,
            ts_start=ts_first,
            ts_end=ts_last,
            duration_ms=_duration_ms(result_event),
            status=attempt_status,
            status_derived=result_event is None,
            error=attempt_error,
            attributes=attempt_attrs,
        )

        session_ids = _ordered_session_ids(events)
        session_span_by_id: dict[str, str] = {}
        for session_id in session_ids:
            session_events = [e for e in events if e.get("session_id") == session_id]
            if not session_events:
                continue
            session_span_id = compute_span_id(span_source, str(jsonl_path), session_id)
            session_span_by_id[session_id] = session_span_id
            session_ts_first = _first_timestamp(session_events) or ts_first
            session_ts_last = _last_timestamp(session_events) or ts_last
            yield TraceEvent(
                trace_id=trace_id,
                span_id=session_span_id,
                parent_span_id=attempt_span_id,
                name=f"agent_session/{session_id[:8]}",
                kind="agent_session",
                source=span_source,
                ts_start=session_ts_first,
                ts_end=session_ts_last,
                status="ok" if result_event is not None else "unknown",
                attributes={
                    "step.id": step_id,
                    "item.key": item_key,
                    "agent.session_id": session_id,
                },
            )

            yield from _tool_spans(
                session_events,
                trace_id=trace_id,
                source=span_source,
                jsonl_path=jsonl_path,
                parent_span_id=session_span_id,
                step_id=step_id,
                item_key=item_key,
            )

        # Internal events span session boundaries (compaction typically has no
        # session_id; rate_limit_event may or may not). Attach to the matching
        # session if known, otherwise to the attempt span as parent.
        yield from _internal_spans(
            events,
            trace_id=trace_id,
            source=span_source,
            jsonl_path=jsonl_path,
            attempt_span_id=attempt_span_id,
            session_span_by_id=session_span_by_id,
            step_id=step_id,
            item_key=item_key,
        )


def _attempt_token_totals(result_event: Mapping[str, Any]) -> dict[str, Any]:
    """Whole-attempt token totals, via the shared Claude normalizer.

    ``models`` lists the models seen, since one attempt routinely spans several
    and no single ``adapter.model`` describes it.
    """
    usage = claude_attempt_usage(result_event)
    if not usage.measured:
        return {
            "input": None,
            "output": None,
            "cache_read": None,
            "cache_write": None,
            "models": [],
        }
    return {
        "input": usage.input_tokens,
        "output": usage.output_tokens,
        "cache_read": usage.cache_read_tokens,
        "cache_write": usage.cache_write_tokens,
        "models": usage.models,
    }


def _parse_step_and_item_key(jsonl_path: Path, *, run_dir: Path) -> tuple[str, str]:
    """Extract ``(step_id, item_key)`` from the path. Path shape:
    ``<run-dir>/.logs/tasks/<step>/<item-key>/<filename>.jsonl``.
    """
    try:
        rel = jsonl_path.relative_to(run_dir / ".logs" / "tasks")
    except ValueError:
        return ("unknown", "unknown")
    parts = rel.parts
    step = parts[0] if len(parts) >= 1 else "unknown"
    item_key = parts[1] if len(parts) >= 2 else "unknown"
    return step, item_key


def _load_invocation_sidecar(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _source_from_invocation(invocation: dict[str, Any], *, default: str) -> str:
    metadata = invocation.get("metadata")
    # Prefer the canonical `adapter_trace_source` written by P0.2; falls back
    # to the older `adapter` / `adapter_type` keys for sidecars from prior runs.
    if isinstance(metadata, dict):
        trace_source = metadata.get("adapter_trace_source")
        if isinstance(trace_source, str) and trace_source:
            return trace_source
    adapter = None
    if isinstance(metadata, dict):
        adapter = metadata.get("adapter") or metadata.get("adapter_type")
    if not isinstance(adapter, str) or not adapter:
        argv = invocation.get("argv")
        if isinstance(argv, list) and argv:
            first = argv[0]
            if isinstance(first, str):
                adapter = first
    if not isinstance(adapter, str):
        return default
    normalized = adapter.lower().replace("_", "-")
    if "codex" in normalized:
        return "codex-agent"
    if "claude" in normalized:
        return "claude-agent"
    return default


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    yield from iter_jsonl_objects(path)


def _ordered_session_ids(events: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for e in events:
        sid = e.get("session_id")
        if isinstance(sid, str) and sid not in seen_set:
            seen.append(sid)
            seen_set.add(sid)
    return seen


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


def _duration_ms(result_event: dict[str, Any] | None) -> float | None:
    if result_event is None:
        return None
    val = result_event.get("duration_ms")
    return float(val) if isinstance(val, (int, float)) else None


def _classify_attempt_status(
    result_event: dict[str, Any] | None, events: list[dict[str, Any]]
) -> SpanStatus:
    if result_event is None:
        return "error"
    if result_event.get("is_error"):
        return "error"
    has_rate_limit = any(e.get("type") == "rate_limit_event" for e in events)
    if has_rate_limit:
        return "partial"
    return "ok"


def _attempt_error_payload(result_event: dict[str, Any] | None) -> dict[str, Any]:
    """Build the attempt span's ``error`` dict from a failed result event.

    Shape: ``{"code": <AgentErrorCode>, "message": <result.result text>}``.
    The message is truncated to 500 chars to keep span payloads bounded.
    When ``result_event`` is None (crashed attempt with no result line),
    code falls back to ``agent_error`` and message is omitted.
    """
    result_text: str | None = None
    if isinstance(result_event, dict):
        raw = result_event.get("result")
        if isinstance(raw, str):
            result_text = raw
    code = classify_agent_error(result_text)
    payload: dict[str, Any] = {"code": code}
    if result_text:
        payload["message"] = result_text[:500]
    return payload


def _tool_spans(
    session_events: list[dict[str, Any]],
    *,
    trace_id: str,
    source: str,
    jsonl_path: Path,
    parent_span_id: str,
    step_id: str,
    item_key: str,
) -> Iterable[TraceEvent]:
    """Pair ``tool_use`` blocks with their matching ``tool_result`` blocks
    and emit one ``tool_call`` span per pair. Unmatched ``tool_use`` blocks
    (typically a crashed attempt) emit a span with ``status=error``.
    """
    pending: dict[str, tuple[dict[str, Any], str]] = {}
    for event in session_events:
        ts = event.get("timestamp")
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use":
                tool_use_id = block.get("id")
                if isinstance(tool_use_id, str):
                    pending[tool_use_id] = (block, ts if isinstance(ts, str) else "")
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if not isinstance(tool_use_id, str):
                    continue
                pair = pending.pop(tool_use_id, None)
                use_block, use_ts = pair or ({}, "")
                yield _tool_call_span(
                    use_block=use_block,
                    use_ts=use_ts,
                    result_block=block,
                    result_ts=ts if isinstance(ts, str) else None,
                    trace_id=trace_id,
                    source=source,
                    jsonl_path=jsonl_path,
                    parent_span_id=parent_span_id,
                    step_id=step_id,
                    item_key=item_key,
                    tool_use_id=tool_use_id,
                )

    # Unmatched tool_uses (crashed attempt — no tool_result for them)
    for tool_use_id, (use_block, use_ts) in pending.items():
        yield _tool_call_span(
            use_block=use_block,
            use_ts=use_ts,
            result_block=None,
            result_ts=None,
            trace_id=trace_id,
            source=source,
            jsonl_path=jsonl_path,
            parent_span_id=parent_span_id,
            step_id=step_id,
            item_key=item_key,
            tool_use_id=tool_use_id,
        )


def _tool_call_span(
    *,
    use_block: dict[str, Any],
    use_ts: str,
    result_block: dict[str, Any] | None,
    result_ts: str | None,
    trace_id: str,
    source: str,
    jsonl_path: Path,
    parent_span_id: str,
    step_id: str,
    item_key: str,
    tool_use_id: str,
) -> TraceEvent:
    tool_name = use_block.get("name") or "unknown"
    tool_input = use_block.get("input") if isinstance(use_block.get("input"), dict) else {}
    attrs: dict[str, Any] = {
        "step.id": step_id,
        "item.key": item_key,
        "tool.use_id": tool_use_id,
    }
    attrs.update(
        tool_usage_attrs(
            tool__name=tool_name,
            raw_log_ref=f"{jsonl_path}#tool_use:{tool_use_id}",
        )
    )
    if isinstance(tool_input, dict):
        for narrow_key in ("file_path", "url", "command", "pattern", "path"):
            if narrow_key in tool_input:
                attrs[f"tool.input.{narrow_key}"] = tool_input[narrow_key]
        attrs.update(tool_usage_classification(str(tool_name), tool_input))

    is_error = bool(result_block and result_block.get("is_error"))
    status: SpanStatus
    error: dict[str, Any] | None
    if result_block is None:
        status = "error"
        error = {"kind": "unmatched_tool_use"}
    elif is_error:
        status = "error"
        error_payload = result_block.get("content")
        error = {"kind": "tool_error"}
        if isinstance(error_payload, str):
            error["message"] = error_payload[:500]
    else:
        status = "ok"
        error = None
        if isinstance(result_block.get("content"), str):
            attrs["tool.result.size_bytes"] = len(result_block["content"])

    span_id = compute_span_id(source, str(jsonl_path), tool_use_id)
    return TraceEvent(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=str(tool_name),
        kind="tool_call",
        source=source,
        ts_start=use_ts or result_ts or "",
        ts_end=result_ts,
        status=status,
        status_derived=False,
        error=error,
        attributes=attrs,
    )


def _internal_spans(
    events: list[dict[str, Any]],
    *,
    trace_id: str,
    source: str,
    jsonl_path: Path,
    attempt_span_id: str,
    session_span_by_id: dict[str, str],
    step_id: str,
    item_key: str,
) -> Iterable[TraceEvent]:
    """Emit ``internal`` spans for non-tool operational events: compaction,
    rate_limit_event, hook_response with non-zero exit.

    Compaction events typically have no ``session_id`` and parent to the
    attempt span; events with a known ``session_id`` parent to the
    matching ``agent_session`` span.
    """
    for idx, event in enumerate(events):
        event_type = event.get("type")
        ts = event.get("timestamp")
        if not isinstance(ts, str):
            continue
        session_id = event.get("session_id")
        parent_span_id = (
            session_span_by_id.get(session_id) if isinstance(session_id, str) else None
        ) or attempt_span_id
        if event_type == "compaction":
            yield TraceEvent(
                trace_id=trace_id,
                span_id=compute_span_id(source, str(jsonl_path), "compaction", idx),
                parent_span_id=parent_span_id,
                name="compaction",
                kind="internal",
                source=source,
                ts_start=ts,
                status="ok",
                attributes={
                    "step.id": step_id,
                    "item.key": item_key,
                    "internal.kind": "compaction",
                    "internal.original_size": event.get("original_size"),
                    "internal.original_lines": event.get("original_lines"),
                },
            )
        elif event_type == "rate_limit_event":
            yield TraceEvent(
                trace_id=trace_id,
                span_id=compute_span_id(source, str(jsonl_path), "rate_limit", idx),
                parent_span_id=parent_span_id,
                name="rate_limit_event",
                kind="internal",
                source=source,
                ts_start=ts,
                status="partial",
                attributes={
                    "step.id": step_id,
                    "item.key": item_key,
                    "internal.kind": "rate_limit_event",
                },
            )
