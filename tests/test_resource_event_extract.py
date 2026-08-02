"""Tests for per-line ResourceEvent extraction (F2, spec §4.4.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaproc.engine.resource_rollup import (
    ResourceBuildResult,
    build_resource_artifacts,
    write_resource_artifacts,
)
from metaproc.logutil.parsing import LogEvent, LogFile
from metaproc.logutil.resource_event_extract import extract_resource_events
from metaproc.models.resources import (
    CoverageState,
    HierarchyRef,
    ItemCompleteEvent,
    ItemFailEvent,
    ItemStartEvent,
    MeteredQuantity,
    MeterKey,
    ProviderMeterObservation,
    ProviderRef,
    ResourcesDocument,
    ToolCallEvent,
    UsageEvent,
    WaitEvent,
)
from metaproc.viz_loader import load_plan_bundle

_PARENT_PROCESS = """\
---
process:
  name: parent
  steps:
    - id: predict
      mode: code
      handler: metaproc.code_steps.scaffold
---

Parent body.
"""


def test_extract_resource_events_emits_process_item_lifecycle(tmp_path: Path) -> None:
    log_path = tmp_path / "process-events.jsonl"
    log_file = LogFile(log_path, color_idx=0)
    event = LogEvent(
        kind="item_complete",
        summary="completed",
        adapter="process",
        timestamp="2026-07-27T12:00:00+00:00",
        raw={
            "event": "item_complete",
            "item_key": "item-1",
            "step_id": "analyze",
            "elapsed_s": 1.5,
        },
    )

    events = extract_resource_events(
        log_path=log_path,
        log_file=log_file,
        log_events=[event],
        hierarchy=HierarchyRef(run_id="run-1"),
        source_kind="process_events",
        source_path=str(log_path),
    )

    assert len(events) == 1
    assert isinstance(events[0], ItemCompleteEvent)
    assert events[0].hierarchy.item_key == "item-1"
    assert events[0].metrics.wall_time_s == 1.5


def test_extract_resource_events_skips_malformed_process_item(tmp_path: Path) -> None:
    log_path = tmp_path / "process-events.jsonl"
    event = LogEvent(
        kind="item_complete",
        summary="malformed",
        adapter="process",
        raw={"event": "item_complete", "item_key": 123, "step_id": "analyze"},
    )

    events = extract_resource_events(
        log_path=log_path,
        log_file=LogFile(log_path, color_idx=0),
        log_events=[event],
        hierarchy=HierarchyRef(run_id="run-1"),
        source_kind="process_events",
        source_path=str(log_path),
    )

    assert events == []


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def _make_pi_log_with_tool_call(path: Path) -> None:
    """Pi log with one tool_execution_start/end pair + one rate_limit + agent_end."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {"type": "agent_start", "model": "claude-opus-4-7", "timestamp": "2026-04-24T12:00:00Z"}
        ),
        json.dumps(
            {
                "type": "tool_execution_start",
                "toolName": "Bash",
                "executionId": "ex-1",
                "args": {"command": "ls"},
                "timestamp": "2026-04-24T12:00:01Z",
            }
        ),
        json.dumps(
            {
                "type": "tool_execution_end",
                "toolName": "Bash",
                "executionId": "ex-1",
                "isError": False,
                "result": {"content": "file.txt"},
                "timestamp": "2026-04-24T12:00:03Z",
            }
        ),
        json.dumps(
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "claude-opus-4-7",
                        "provider": "anthropic",
                        "usage": {
                            "input": 100,
                            "output": 50,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "cost": {"total": 0.42},
                        },
                    }
                ],
                "timestamp": "2026-04-24T12:00:05Z",
            }
        ),
    ]
    path.write_text("\n".join(lines) + "\n")


def test_build_resource_artifacts_returns_events_alongside_document(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    assert isinstance(result, ResourceBuildResult)
    assert result.document.run_id == "run-1"
    assert len(result.events) >= 2  # at least tool_call + usage


def test_tool_execution_pair_emits_tool_call_event(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    tool_calls = [e for e in result.events if isinstance(e, ToolCallEvent)]
    assert len(tool_calls) == 1
    tc = tool_calls[0]
    assert tc.hierarchy.tool_name == "Bash"
    assert tc.metrics.tool_calls == 1
    assert tc.metrics.tool_failures == 0
    assert tc.metrics.tool_exec_s == pytest.approx(2.0)
    assert tc.taxonomy.tool_path == ["execution", "tool", "bash", "Bash"]


def test_pi_agent_end_emits_usage_event(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    usages = [e for e in result.events if isinstance(e, UsageEvent)]
    assert len(usages) == 1
    u = usages[0]
    # Pi parser routes input/output token totals through UsageStats.
    assert u.metrics.input_tokens == 100
    assert u.metrics.output_tokens == 50
    assert u.provider is not None
    assert u.provider.provider == "anthropic"
    request_meter = next(meter for meter in u.meters if meter.key.meter == "requests")
    assert request_meter.coverage is CoverageState.UNMEASURED
    assert request_meter.actual_quantity is None


def test_gemini_usage_rollup_includes_billed_thinking_tokens_and_estimated_cost(
    tmp_path: Path,
) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    log_path = run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {
                    "type": "init",
                    "model": "gemini-3.6-flash",
                    "session_id": "ses_fixture",
                },
                {
                    "type": "result",
                    "status": "success",
                    "stats": {
                        "total_tokens": 1500,
                        "input_tokens": 1000,
                        "input": 800,
                        "cached": 200,
                        "output_tokens": 100,
                        "models": {
                            "gemini-3.6-flash": {
                                "total_tokens": 1500,
                                "input_tokens": 1000,
                                "input": 800,
                                "cached": 200,
                                "output_tokens": 100,
                            }
                        },
                    },
                },
            )
        )
        + "\n"
    )

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    usage = next(
        event
        for event in result.events
        if isinstance(event, UsageEvent) and event.metrics.list_cost_usd is not None
    )

    assert usage.metrics.input_tokens == 800
    assert usage.metrics.cache_read_tokens == 200
    assert usage.metrics.output_tokens == 500
    assert usage.metrics.list_cost_usd == pytest.approx(0.00498)
    assert result.document.hierarchy_root.total_metrics.list_cost_usd == pytest.approx(0.00498)


def test_registered_provider_meter_source_emits_authoritative_nested_observation(
    tmp_path: Path,
) -> None:
    class FakeProviderMeterSource:
        name = "fake-provider-meter"

        def extract(self, **_kwargs: object) -> list[ProviderMeterObservation]:
            return [
                ProviderMeterObservation(
                    event_id="evt_nested-1",
                    provider=ProviderRef(
                        provider="serpapi",
                        product="google_trends",
                        request_id="req_123",
                    ),
                    api_requests=1,
                    meters=[
                        MeteredQuantity(
                            key=MeterKey(
                                provider="serpapi",
                                product="google_trends",
                                meter="credits",
                                unit="credit",
                            ),
                            coverage=CoverageState.MEASURED,
                            actual_quantity=1,
                        )
                    ],
                )
            ]

    log_path = tmp_path / "session.jsonl"
    events = extract_resource_events(
        log_path=log_path,
        log_file=LogFile(log_path, color_idx=0),
        log_events=[],
        hierarchy=HierarchyRef(run_id="run-1"),
        source_kind="agent_log",
        source_path=".logs/session.jsonl",
        provider_meter_sources=[FakeProviderMeterSource()],
    )

    assert len(events) == 1
    observation = events[0]
    assert isinstance(observation, UsageEvent)
    assert observation.event_id == "evt_nested-1"
    assert observation.metrics.api_requests == 1
    assert observation.provider is not None
    assert observation.provider.request_id == "req_123"
    assert observation.meters[0].actual_quantity == 1


def test_event_carries_step_and_item_attribution(tmp_path: Path) -> None:
    """Events emitted from a fan-out log carry step_node_id + item_key."""
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    for ev in result.events:
        assert ev.hierarchy.run_id == "run-1"
        assert ev.hierarchy.step_node_id == "predict"
        assert ev.hierarchy.item_key == "AAPL"
        # Source pointer should record the file path relative to the run dir.
        assert ev.source.path == "predict/AAPL/.logs/session.jsonl"


def test_process_item_lifecycle_records_emit_resource_events(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    process_events = run_dir / ".logs" / "process-events.jsonl"
    process_events.parent.mkdir(parents=True)
    process_events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "item_start",
                        "ts": "2026-04-24T12:00:00Z",
                        "step_id": "predict",
                        "item_key": "AAPL",
                        "worker_id": "w-1",
                    }
                ),
                json.dumps(
                    {
                        "event": "item_complete",
                        "ts": "2026-04-24T12:00:03Z",
                        "step_id": "predict",
                        "item_key": "AAPL",
                        "worker_id": "w-1",
                        "elapsed_s": 3.0,
                    }
                ),
                json.dumps(
                    {
                        "event": "item_fail",
                        "ts": "2026-04-24T12:00:04Z",
                        "step_id": "predict",
                        "item_key": "MSFT",
                        "worker_id": "w-1",
                        "error": "exit code 1",
                        "failure_class": "worker_exit",
                    }
                ),
            ]
        )
        + "\n"
    )

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")

    starts = [e for e in result.events if isinstance(e, ItemStartEvent)]
    completes = [e for e in result.events if isinstance(e, ItemCompleteEvent)]
    failures = [e for e in result.events if isinstance(e, ItemFailEvent)]
    assert len(starts) == 1
    assert len(completes) == 1
    assert len(failures) == 1
    assert starts[0].hierarchy.step_node_id == "predict"
    assert starts[0].hierarchy.item_key == "AAPL"
    assert starts[0].hierarchy.worker_id == "w-1"
    assert completes[0].metrics.wall_time_s == pytest.approx(3.0)
    assert failures[0].error == "exit code 1"
    assert failures[0].failure_class == "worker_exit"


def test_no_events_when_log_is_silent(tmp_path: Path) -> None:
    """A log file with no usage / tools / rate-limits produces no events."""
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    silent = run_dir / "predict" / ".logs" / "session.jsonl"
    silent.parent.mkdir(parents=True)
    silent.write_text(
        json.dumps({"type": "agent_start", "model": "x", "timestamp": "2026-04-24T12:00:00Z"})
        + "\n"
    )
    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    # No tool calls, no rate-limits, no terminal usage stats — no events.
    assert result.events == []


def test_throttle_event_is_emitted_for_blocked_rate_limit(tmp_path: Path) -> None:
    """Claude rate_limit_event with status=blocked + a follow-up event yields a WaitEvent."""
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    log_path = run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl"
    log_path.parent.mkdir(parents=True)
    lines = [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "model": "claude-opus-4-7",
                "session_id": "abc",
                "timestamp": "2026-04-24T12:00:00Z",
            }
        ),
        json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "blocked", "rateLimitType": "rate_limits"},
                "timestamp": "2026-04-24T12:00:01Z",
            }
        ),
        # next event closes the throttling window
        json.dumps({"type": "system", "subtype": "init", "timestamp": "2026-04-24T12:00:05Z"}),
    ]
    log_path.write_text("\n".join(lines) + "\n")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    waits = [e for e in result.events if isinstance(e, WaitEvent)]
    assert len(waits) == 1
    w = waits[0]
    assert w.metrics.wait_throttling_s == pytest.approx(4.0)
    assert w.taxonomy.time_kind_path == ["waiting", "throttling", "rate_limits", "api"]


# ── F3: persistence ────────────────────────────────────────────────


def test_build_with_write_persists_both_artifacts(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1", write=True)
    assert result.document_path is not None
    assert result.events_path is not None
    assert result.document_path.exists()
    assert result.events_path.exists()

    parsed_doc = ResourcesDocument.model_validate_json(result.document_path.read_text())
    assert parsed_doc.run_id == "run-1"
    # resource-events.jsonl has one line per event.
    event_lines = result.events_path.read_text().strip().splitlines()
    assert len(event_lines) == len(result.events)


def test_write_resource_artifacts_atomically_replaces(tmp_path: Path) -> None:
    """A second write should leave the file in a consistent state — never empty."""

    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    write_resource_artifacts(result)
    assert result.document_path is not None
    first_size = result.document_path.stat().st_size

    # Touch one log and rebuild — still atomic.
    new_log = run_dir / "predict" / "MSFT" / ".logs" / "session.jsonl"
    _make_pi_log_with_tool_call(new_log)
    result2 = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    write_resource_artifacts(result2)
    assert result2.document_path is not None
    second_size = result2.document_path.stat().st_size
    assert second_size >= first_size


# ── F5: file: and tool: leaf node creation ─────────────────────────


def test_tool_call_event_creates_file_and_tool_leaf(tmp_path: Path) -> None:
    """Per-event metrics route through file → tool leaves and roll up via totals."""
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    # Walk the tree: run → process:root → predict (step) → AAPL (item) → file: → tool:
    process_root = result.document.hierarchy_root.children[0]
    step = process_root.children[0]
    item = next((c for c in step.children if c.node_type == "item"), None)
    assert item is not None
    file_node = next((c for c in item.children if c.node_type == "file"), None)
    assert file_node is not None
    assert file_node.label == "session.jsonl"
    tool_node = next((c for c in file_node.children if c.node_type == "tool"), None)
    assert tool_node is not None
    assert tool_node.label == "Bash"
    # Tool leaf owns the per-call metrics; bottom-up sum makes them visible at the run.
    assert tool_node.self_metrics.tool_calls == 1
    assert tool_node.self_metrics.tool_exec_s == pytest.approx(2.0)
    assert result.document.hierarchy_root.total_metrics.tool_calls == 1
