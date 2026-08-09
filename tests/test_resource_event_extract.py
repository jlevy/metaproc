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
from metaproc.logutil.tool_failures import FailureKind
from metaproc.models.resources import (
    HierarchyRef,
    ItemCompleteEvent,
    ItemFailEvent,
    ItemStartEvent,
    ResourcesDocument,
    StepCompleteEvent,
    StepFailEvent,
    StepStartEvent,
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


def test_extract_resource_events_emits_process_step_lifecycle(tmp_path: Path) -> None:
    log_path = tmp_path / "process-events.jsonl"
    log_file = LogFile(log_path, color_idx=0)
    log_events = [
        LogEvent(
            kind="step_start",
            summary="started",
            adapter="process",
            timestamp="2026-07-27T12:00:00+00:00",
            raw={
                "event": "step_start",
                "step_id": "analyze",
                "step_node_id": "child::analyze",
                "process_node_id": "process:child",
                "mode": "code",
            },
        ),
        LogEvent(
            kind="step_complete",
            summary="completed",
            adapter="process",
            timestamp="2026-07-27T12:00:02+00:00",
            raw={
                "event": "step_complete",
                "step_id": "analyze",
                "step_node_id": "child::analyze",
                "process_node_id": "process:child",
                "elapsed_s": 2.0,
            },
        ),
        LogEvent(
            kind="step_fail",
            summary="failed",
            adapter="process",
            timestamp="2026-07-27T12:00:05+00:00",
            raw={
                "event": "step_fail",
                "step_id": "cleanup",
                "step_node_id": "child::cleanup",
                "process_node_id": "process:child",
                "elapsed_s": 3.0,
                "error": "boom",
            },
        ),
    ]

    events = extract_resource_events(
        log_path=log_path,
        log_file=log_file,
        log_events=log_events,
        hierarchy=HierarchyRef(
            run_id="run-1",
            file_path=".logs/process-events.jsonl",
        ),
        source_kind="process_events",
        source_path=str(log_path),
    )

    assert [type(event) for event in events] == [
        StepStartEvent,
        StepCompleteEvent,
        StepFailEvent,
    ]
    assert events[0].metrics.wall_time_s is None
    assert events[1].metrics.wall_time_s == 2.0
    assert events[2].metrics.wall_time_s == 3.0
    failure = events[2]
    assert isinstance(failure, StepFailEvent)
    assert failure.error == "boom"
    assert [event.hierarchy.step_node_id for event in events] == [
        "child::analyze",
        "child::analyze",
        "child::cleanup",
    ]
    assert all(event.hierarchy.file_path is None for event in events)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("tool_call", FailureKind.ADAPTER_DROPPED_CALL),
        ("tool_result", FailureKind.UNKNOWN),
    ],
)
def test_orphan_tool_records_remain_explicit_in_resource_ledger(
    tmp_path: Path,
    kind: str,
    expected: FailureKind,
) -> None:
    log_path = tmp_path / "orphan.jsonl"
    events = extract_resource_events(
        log_path=log_path,
        log_file=LogFile(log_path, color_idx=0),
        log_events=[
            LogEvent(
                kind=kind,
                summary="[call:Bash]" if kind == "tool_call" else "[result:Bash]",
                adapter="pi",
                tool_name="Bash",
            )
        ],
        hierarchy=HierarchyRef(run_id="run-1"),
        source_kind="agent_log",
        source_path="orphan.jsonl",
    )

    tool_event = next(event for event in events if isinstance(event, ToolCallEvent))
    assert tool_event.failure_kind is expected


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


def _make_gemini_log_with_residual_tool_count(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "init", "session_id": "session-1", "model": "gemini-test"},
        {
            "type": "tool_use",
            "tool_id": "tool-1",
            "tool_name": "shell",
            "parameters": {"command": "one"},
        },
        {"type": "tool_result", "tool_id": "tool-1", "status": "success", "output": "ok"},
        {
            "type": "tool_use",
            "tool_id": "tool-2",
            "tool_name": "shell",
            "parameters": {"command": "two"},
        },
        {"type": "tool_result", "tool_id": "tool-2", "status": "success", "output": "ok"},
        {
            "type": "result",
            "status": "success",
            "stats": {
                "tool_calls": 3,
                "models": {
                    "gemini-test": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "cached": 0,
                    }
                },
            },
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def test_build_resource_artifacts_returns_events_alongside_document(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    assert isinstance(result, ResourceBuildResult)
    assert result.document.run_id == "run-1"
    assert len(result.events) >= 2  # at least tool_call + usage


def test_repeated_extraction_has_stable_event_ids_without_source_timestamps(
    tmp_path: Path,
) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    first = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    second = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")

    assert [event.event_id for event in first.events] == [event.event_id for event in second.events]


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
    usages = [
        event
        for event in result.events
        if isinstance(event, UsageEvent) and event.metrics.input_tokens is not None
    ]
    assert len(usages) == 1
    u = usages[0]
    # Pi parser routes input/output token totals through UsageStats.
    assert u.metrics.input_tokens == 100
    assert u.metrics.output_tokens == 50
    assert u.metrics.actual_cost_usd is None
    assert u.metrics.list_cost_usd == pytest.approx(0.42)


def test_terminal_tool_total_contributes_only_positive_residual(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_gemini_log_with_residual_tool_count(
        run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl"
    )

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    usage = next(event for event in result.events if isinstance(event, UsageEvent))

    assert len([event for event in result.events if isinstance(event, ToolCallEvent)]) == 2
    assert usage.metrics.tool_calls == 1
    assert result.document.hierarchy_root.total_metrics.tool_calls == 3


@pytest.mark.parametrize(("tool_stats", "expected"), [({"tool_calls": 0}, 0), ({}, None)])
def test_terminal_tool_call_evidence_preserves_zero_vs_unknown(
    tmp_path: Path,
    tool_stats: dict[str, int],
    expected: int | None,
) -> None:
    log_path = tmp_path / "gemini.jsonl"
    log_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "init", "session_id": "session-1", "model": "gemini-test"}),
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "stats": {
                            **tool_stats,
                            "models": {
                                "gemini-test": {
                                    "input_tokens": 1,
                                    "output_tokens": 0,
                                    "cached": 0,
                                }
                            },
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    log_file = LogFile(log_path, color_idx=0)
    log_events = log_file.read_new_events()
    if log_file.done:
        log_events.extend(log_file.flush())

    events = extract_resource_events(
        log_path=log_path,
        log_file=log_file,
        log_events=log_events,
        hierarchy=HierarchyRef(run_id="run-1"),
        source_kind="agent_log",
        source_path="gemini.jsonl",
    )
    usage = next(
        event
        for event in events
        if isinstance(event, UsageEvent) and event.metrics.input_tokens is not None
    )

    assert usage.metrics.tool_calls == expected


def test_agent_provider_meters_flow_through_ledger_and_coverage(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_pi_log_with_tool_call(run_dir / "predict" / "AAPL" / ".logs" / "session.jsonl")

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    rollups = {rollup.key.meter: rollup for rollup in result.document.meter_rollups}

    assert rollups["agent_invocations"].actual_quantity == 1
    assert rollups["api_requests"].coverage.value == "unmeasured"
    assert rollups["model_turns"].coverage.value == "unmeasured"
    assert {key.meter for key in result.document.coverage_gaps} == {
        "api_requests",
        "model_turns",
    }


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


def test_step_lifecycle_wall_time_rolls_up_to_each_owning_step(tmp_path: Path) -> None:
    process = _write(
        tmp_path,
        "parent/test.process.md",
        """---
process:
  name: parent
  steps:
    - id: analyze
      mode: code
      handler: metaproc.code_steps.scaffold
    - id: cleanup
      mode: code
      handler: metaproc.code_steps.scaffold
---

Parent body.
""",
    )
    bundle = load_plan_bundle(process)
    run_dir = tmp_path / "runs" / "2026-04-21"
    process_events = run_dir / ".logs" / "process-events.jsonl"
    process_events.parent.mkdir(parents=True)
    process_events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "step_complete",
                        "ts": "2026-04-24T12:00:02Z",
                        "step_id": "analyze",
                        "elapsed_s": 2.0,
                    }
                ),
                json.dumps(
                    {
                        "event": "step_fail",
                        "ts": "2026-04-24T12:00:05Z",
                        "step_id": "cleanup",
                        "elapsed_s": 3.0,
                        "error": "boom",
                    }
                ),
            ]
        )
        + "\n"
    )

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")

    steps = {
        node.node_id: node
        for node in result.document.hierarchy_root.children[0].children
        if node.node_type == "step"
    }
    assert steps["analyze"].self_metrics.wall_time_s == 2.0
    assert steps["cleanup"].self_metrics.wall_time_s == 3.0
    assert not any(child.node_type == "file" for child in steps["analyze"].children)
    assert not any(child.node_type == "file" for child in steps["cleanup"].children)


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
