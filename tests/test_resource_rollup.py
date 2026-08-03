"""Tests for the keystone resource rollup builder (B6, spec §5 + §7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaproc.engine.resource_rollup import build_resources_document
from metaproc.io.state_io import write_status_at
from metaproc.models.resources import ResourcesDocument
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
    assert composite_step.total_metrics.list_cost_usd == pytest.approx(0.42)
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
    assert doc.hierarchy_root.total_metrics.list_cost_usd == pytest.approx(0.42)
    assert doc.hierarchy_root.total_metrics.input_tokens == 100
    # Composite step contributes its self metrics to the run total.
    process_root = doc.hierarchy_root.children[0]
    assert process_root.total_metrics.list_cost_usd == pytest.approx(0.42)


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
    assert process_root.total_metrics.list_cost_usd == pytest.approx(0.42)


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
    ].total_metrics.list_cost_usd == pytest.approx(0.42)


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
    assert leaf_step.total_metrics.list_cost_usd == pytest.approx(0.42)
    assert any(child.node_id == "run_child::leaf::sonnet/AAPL" for child in leaf_step.children)
    assert doc.source_logs[0].owner_node_id == "run_child::leaf"
