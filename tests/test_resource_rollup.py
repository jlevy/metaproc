"""Tests for the keystone resource rollup builder (B6, spec §5 + §7)."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from metaproc.engine import resource_rollup
from metaproc.engine.resource_rollup import (
    aggregate_meter_rollups,
    build_resource_artifacts,
    build_resources_document,
)
from metaproc.io.state_io import write_status_at
from metaproc.models.resources import (
    CoverageState,
    HierarchyRef,
    MeteredQuantity,
    MeterKey,
    Metrics,
    ProviderRef,
    ResourceEvent,
    ResourcesDocument,
    SourceRef,
    UsageEvent,
)
from metaproc.models.runtime import StatusRecord
from metaproc.viz_loader import load_plan_bundle

# Use the same composite parent/child fixture B5's tests pin down so the
# rollup contract is validated against the same canonical hierarchy shape.
_PARENT_PROCESS = """\
---
process:
  name: parent
  deps:
    child_proc:
      path: child/test.process.md
      role: process
      as: path
  steps:
    - id: run_child
      mode: composite
      uses: deps.child_proc
      with:
        CUTOFF_DATE: "{{CUTOFF_DATE}}"
---

Parent body.
"""

_CHILD_PROCESS = """\
---
process:
  name: child
  steps:
    - id: leaf
      mode: code
      handler: metaproc.code_steps.scaffold
---

Child body.
"""

_PREDICT_PROCESS = """\
---
process:
  name: predict
  steps:
    - id: predict
      mode: code
      handler: metaproc.code_steps.scaffold
---

Predict body.
"""


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def _make_claude_log(path: Path) -> None:
    """Write a minimal Claude-format JSONL session that yields known stats."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
                "type": "result",
                "result": "ok",
                "subtype": "success",
                "is_error": False,
                "total_cost_usd": 0.42,
                "duration_ms": 1500,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 25,
                },
                "timestamp": "2026-04-24T12:00:01.5Z",
            }
        ),
    ]
    path.write_text("\n".join(lines) + "\n")


def test_document_has_root_run_node_and_v2_schema(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    run_dir.mkdir(parents=True)

    doc = build_resources_document(bundle=bundle, run_dir=run_dir, run_id="run-1")

    assert doc.run_id == "run-1"
    assert doc.hierarchy_root.node_type == "run"
    assert doc.hierarchy_root.node_id == "run-1"
    assert doc.source_logs == []  # no logs yet
    payload = doc.model_dump_json(by_alias=True)
    assert json.loads(payload)["schema"] == "metaproc.resources/v2"


def _meter(unit: str, coverage: CoverageState, quantity: float | None) -> MeteredQuantity:
    values: dict[str, object] = {
        "key": MeterKey(
            provider="serpapi",
            product="google_trends",
            meter="requests",
            unit=unit,
        ),
        "coverage": coverage,
    }
    if coverage is CoverageState.MEASURED:
        values["actual_quantity"] = quantity
    elif coverage is CoverageState.ESTIMATED:
        values["estimated_quantity"] = quantity
    return MeteredQuantity.model_validate(values)


def test_meter_rollup_uses_exact_keys_and_deduplicates_event_ids() -> None:
    request = _meter("request", CoverageState.MEASURED, 1)
    credit = _meter("credit", CoverageState.MEASURED, 1)

    rollups = aggregate_meter_rollups(
        [
            ("evt_request-1", [request, credit]),
            ("evt_request-1", [request, credit]),
            ("evt_request-2", [request]),
        ]
    )

    assert [(rollup.key.unit, rollup.actual_quantity) for rollup in rollups] == [
        ("credit", 1),
        ("request", 2),
    ]


def test_meter_rollup_preserves_estimates_and_coverage_gaps() -> None:
    rollups = aggregate_meter_rollups(
        [
            ("evt_estimate-1", [_meter("request", CoverageState.ESTIMATED, 2)]),
            ("evt_gap-1", [_meter("request", CoverageState.UNMEASURED, None)]),
        ]
    )

    assert len(rollups) == 1
    rollup = rollups[0]
    assert rollup.coverage is CoverageState.UNMEASURED
    assert rollup.actual_quantity is None
    assert rollup.estimated_quantity == 2
    assert rollup.unmeasured_event_count == 1


def test_meter_rollup_rejects_conflicting_duplicate_event_identity() -> None:
    with pytest.raises(ValueError, match="duplicate resource event identity"):
        aggregate_meter_rollups(
            [
                ("evt_request-1", [_meter("request", CoverageState.MEASURED, 1)]),
                ("evt_request-1", [_meter("request", CoverageState.MEASURED, 2)]),
            ]
        )


def test_builder_deduplicates_provider_events_and_propagates_meters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _write(tmp_path, "predict/test.process.md", _PREDICT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    source_path = run_dir / ".logs" / "provider-events.jsonl"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("{}\n")

    event = UsageEvent(
        event_id="evt_request-1",
        ts=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
        hierarchy=HierarchyRef(run_id="run-1", step_node_id="predict"),
        source=SourceRef(kind="provider", path=".logs/provider-events.jsonl"),
        provider=ProviderRef(provider="serpapi", product="google_trends"),
        metrics=Metrics(api_requests=1),
        meters=[_meter("credit", CoverageState.MEASURED, 1)],
    )

    class FakeResourceSource:
        name = "provider"
        source_kind = "provider"
        adapter = "provider"

        def discover(self, _run_dir: Path) -> Sequence[Path]:
            return [source_path]

        def extract(self, **_kwargs: object) -> Sequence[ResourceEvent]:
            return [event, event.model_copy(deep=True)]

    class FakeRegistry:
        resource_event_sources = [FakeResourceSource()]
        provider_meter_sources: list[object] = []

    monkeypatch.setattr(resource_rollup, "get_plugin_registry", FakeRegistry)

    result = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")

    assert len(result.events) == 1
    assert result.document.hierarchy_root.total_metrics.api_requests == 1
    assert len(result.document.meter_rollups) == 1
    assert result.document.meter_rollups[0].actual_quantity == 1
    assert result.document.hierarchy_root.total_meters == result.document.meter_rollups


def test_generated_event_ids_are_stable_across_rebuilds(tmp_path: Path) -> None:
    parent = _write(tmp_path, "predict/test.process.md", _PREDICT_PROCESS)
    bundle = load_plan_bundle(parent)
    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_claude_log(run_dir / "predict" / ".logs" / "session.jsonl")

    first = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")
    log_path = run_dir / "predict" / ".logs" / "session.jsonl"
    original_mtime = log_path.stat().st_mtime
    os.utime(log_path, (original_mtime + 30, original_mtime + 30))
    second = build_resource_artifacts(bundle=bundle, run_dir=run_dir, run_id="run-1")

    assert [event.event_id for event in first.events] == [event.event_id for event in second.events]
    assert all(re.fullmatch(r"evt_[a-z0-9]{14}", event.event_id or "") for event in first.events)


def test_attributes_agent_log_to_owning_step(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_claude_log(run_dir / "run_child" / ".logs" / "session.jsonl")

    doc = build_resources_document(bundle=bundle, run_dir=run_dir, run_id="run-1")

    # After F5, per-event metrics land on file: leaves under the step. The
    # composite step's total_metrics aggregates the leaf via bottom-up sum.
    process_root = doc.hierarchy_root.children[0]
    composite_step = process_root.children[0]
    assert composite_step.total_metrics.actual_cost_usd == pytest.approx(0.42)
    assert composite_step.total_metrics.input_tokens == 100
    assert composite_step.total_metrics.output_tokens == 50
    assert composite_step.total_metrics.cache_read_tokens == 200
    assert composite_step.total_metrics.cache_write_tokens == 25
    # And the source-log inventory carries one entry pointed at the right node.
    assert len(doc.source_logs) == 1
    assert doc.source_logs[0].owner_node_id == "run_child"
    assert doc.source_logs[0].adapter == "claude"


def test_totals_propagate_bottom_up(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_claude_log(run_dir / "run_child" / ".logs" / "session.jsonl")

    doc = build_resources_document(bundle=bundle, run_dir=run_dir, run_id="run-1")

    # Root run_node total should equal the single leaf's self total.
    assert doc.hierarchy_root.total_metrics.actual_cost_usd == pytest.approx(0.42)
    assert doc.hierarchy_root.total_metrics.input_tokens == 100
    # Composite step contributes its self metrics to the run total.
    process_root = doc.hierarchy_root.children[0]
    assert process_root.total_metrics.actual_cost_usd == pytest.approx(0.42)


def test_unattributed_metrics_capture_logs_outside_run_dir(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    # Place a log at the run root with no step prefix — derive_owner returns
    # process:root which the skeleton has, so attribution should land there.
    _make_claude_log(run_dir / ".logs" / "summary.jsonl")

    doc = build_resources_document(bundle=bundle, run_dir=run_dir, run_id="run-1")

    process_root = doc.hierarchy_root.children[0]
    # The summary log lacks a step prefix, so its events accrue to a file:
    # leaf under the root process node — visible via total_metrics.
    assert process_root.total_metrics.actual_cost_usd == pytest.approx(0.42)


def test_taxonomy_rollups_persist_as_lists(tmp_path: Path) -> None:
    """Spec §4.4.4: PrefixRollup is a list-of-entries (JSON-friendly)."""
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_claude_log(run_dir / "run_child" / ".logs" / "session.jsonl")

    doc = build_resources_document(bundle=bundle, run_dir=run_dir, run_id="run-1")

    # Provider/model rollups are populated from session usage.
    if "provider_path" in doc.taxonomy_rollups:
        rollups = doc.taxonomy_rollups["provider_path"]
        assert isinstance(rollups, list)
        assert all(rollup.path[0] == "provider" for rollup in rollups)
    if "model_path" in doc.taxonomy_rollups:
        rollups = doc.taxonomy_rollups["model_path"]
        assert all(rollup.canonical.startswith("model") for rollup in rollups)


def test_source_log_summary_includes_event_count(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_claude_log(run_dir / "run_child" / ".logs" / "session.jsonl")

    doc = build_resources_document(bundle=bundle, run_dir=run_dir, run_id="run-1")

    summary = doc.source_logs[0].summary
    assert summary.event_count >= 1
    assert summary.source_log_count == 1


def test_round_trip_through_json(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_claude_log(run_dir / "run_child" / ".logs" / "session.jsonl")

    doc = build_resources_document(bundle=bundle, run_dir=run_dir, run_id="run-1")
    raw = doc.model_dump_json(by_alias=True)

    restored = ResourcesDocument.model_validate_json(raw)
    assert restored.run_id == "run-1"
    assert restored.hierarchy_root.children[0].children[
        0
    ].total_metrics.actual_cost_usd == pytest.approx(0.42)


def test_variant_item_status_node_present(tmp_path: Path) -> None:
    """Per-task state under ``<run>/.state/tasks/<step>/<item>/`` produces an item node.

    Log-to-item attribution for session logs that live alongside artifacts
    (``<run>/<step>/<variant>/<item>/.logs/``) is verified by sibling tests;
    here we only assert that the state-derived item node exists.
    """
    parent = _write(tmp_path, "predict/test.process.md", _PREDICT_PROCESS)
    bundle = load_plan_bundle(parent)

    run_dir = tmp_path / "runs" / "2026-04-21"
    state_dir = run_dir / ".state" / "tasks" / "predict" / "AAPL"
    state_dir.mkdir(parents=True)
    write_status_at(
        state_dir,
        StatusRecord(
            run_id="run-1",
            step_id="predict",
            item={"ticker": "AAPL"},
            state="completed",
            attempt=1,
        ),
    )

    doc = build_resources_document(bundle=bundle, run_dir=run_dir, run_id="run-1")

    step = doc.hierarchy_root.children[0].children[0]
    item_ids = [child.node_id for child in step.children if child.node_type == "item"]
    assert "predict::AAPL" in item_ids


def test_composite_child_logs_resolve_to_qualified_child_step(tmp_path: Path) -> None:
    parent = _write(tmp_path, "parent/test.process.md", _PARENT_PROCESS)
    _write(tmp_path, "parent/child/test.process.md", _CHILD_PROCESS)
    bundle = load_plan_bundle(parent, params={"CUTOFF_DATE": "2026-04-21"})

    run_dir = tmp_path / "runs" / "2026-04-21"
    _make_claude_log(run_dir / "run_child" / "leaf" / "sonnet" / "AAPL" / ".logs" / "session.jsonl")

    doc = build_resources_document(bundle=bundle, run_dir=run_dir, run_id="run-1")

    composite_step = doc.hierarchy_root.children[0].children[0]
    nested_process = composite_step.children[0]
    leaf_step = nested_process.children[0]
    assert leaf_step.node_id == "run_child::leaf"
    assert leaf_step.total_metrics.actual_cost_usd == pytest.approx(0.42)
    assert any(child.node_id == "run_child::leaf::sonnet/AAPL" for child in leaf_step.children)
    assert doc.source_logs[0].owner_node_id == "run_child::leaf"
