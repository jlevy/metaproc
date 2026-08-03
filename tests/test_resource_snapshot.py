"""Immutable resource topology and budget launch snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from metaproc.commands.run_process import _write_run_config
from metaproc.engine.resource_snapshot import (
    build_resource_run_snapshot,
    read_resource_run_snapshot,
)
from metaproc.io import read_yaml_file, to_yaml_string
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import Plan, ResolvedStep
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.resource_budget import (
    BudgetMetric,
    BudgetScope,
    BudgetScopeKind,
    ResourceBudgetSpec,
)
from metaproc.models.resource_snapshot import ResourceRunSnapshot, ResourceTopologyNode
from metaproc.viz_loader import load_plan_bundle

_PROCESS = """\
---
process:
  name: snapshot-test
  inputs:
    runs_dir:
      param: RUNS_DIR
      as: string
      required: true
  resource_budgets:
    - budget_id: bud-run-inputs
      metric: input_tokens
      threshold: 100
  steps:
    - id: analyze
      mode: code
      handler: metaproc.code_steps.scaffold
      token_budget: 50
---

Snapshot test.
"""


def _bundle(tmp_path: Path):
    path = tmp_path / "snapshot.process.md"
    path.write_text(_PROCESS)
    return path, load_plan_bundle(path, params={"RUNS_DIR": str(tmp_path / "runs")})


def test_snapshot_contains_lean_topology_and_normalized_budgets(tmp_path: Path) -> None:
    _, bundle = _bundle(tmp_path)

    snapshot = build_resource_run_snapshot(bundle, run_id="snapshot-test/run-1")

    assert snapshot.hierarchy.node_type == "run"
    assert snapshot.hierarchy.children[0].node_type == "process"
    assert snapshot.hierarchy.children[0].children[0].node_id == "analyze"
    assert {budget.metric for budget in snapshot.budgets} == {
        BudgetMetric.INPUT_TOKENS,
        BudgetMetric.TOTAL_TOKENS,
    }
    payload = snapshot.model_dump(mode="json", by_alias=True)
    assert "self_metrics" not in str(payload)


def test_first_run_config_snapshot_is_not_replaced_on_resume(tmp_path: Path) -> None:
    process_path, bundle = _bundle(tmp_path)
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    original = build_resource_run_snapshot(bundle, run_id="snapshot-test/run-1")
    changed = original.model_copy(
        update={
            "budgets": [
                ResourceBudgetSpec(
                    budget_id="bud-changed",
                    scope=BudgetScope(kind=BudgetScopeKind.RUN),
                    metric=BudgetMetric.INPUT_TOKENS,
                    threshold=999,
                )
            ]
        }
    )

    _write_run_config(
        run_dir,
        process_name="snapshot-test",
        process_path=process_path,
        run_id="run-1",
        variables={"RUN_ID": "run-1"},
        backend="local",
        variant=None,
        resource_snapshot=original,
    )
    _write_run_config(
        run_dir,
        process_name="snapshot-test",
        process_path=process_path,
        run_id="run-1",
        variables={"RUN_ID": "run-1"},
        backend="local",
        variant=None,
        resource_snapshot=changed,
    )

    restored = read_resource_run_snapshot(run_dir)
    assert restored == original
    raw = read_yaml_file(run_dir / ".state" / "run-config.yaml")
    assert raw["resources"]["schema"] == "metaproc.resource-snapshot/v1"


def test_legacy_run_without_resource_snapshot_returns_none(tmp_path: Path) -> None:
    run_dir = tmp_path / "legacy"
    config = run_dir / ".state" / "run-config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("process: old\nrun_id: old-1\n")

    assert read_resource_run_snapshot(run_dir) is None


def test_snapshot_reader_rejects_missing_schema_token(tmp_path: Path) -> None:
    process_path, bundle = _bundle(tmp_path)
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    snapshot = build_resource_run_snapshot(bundle, run_id="snapshot-test/run-1")
    config_path = _write_run_config(
        run_dir,
        process_name="snapshot-test",
        process_path=process_path,
        run_id="run-1",
        variables={"RUN_ID": "run-1"},
        backend="local",
        variant=None,
        resource_snapshot=snapshot,
    )
    raw = read_yaml_file(config_path)
    raw["resources"].pop("schema")
    config_path.write_text(to_yaml_string(raw))

    with pytest.raises(ValueError, match="resource snapshot schema token"):
        read_resource_run_snapshot(run_dir)


def test_reused_composite_budget_ids_are_instance_qualified() -> None:
    child_budget = ResourceBudgetSpec.model_validate(
        {
            "budget_id": "bud-child-inputs",
            "metric": "input_tokens",
            "threshold": 10,
        }
    )
    child = PlanBundle(
        plan=Plan(
            process="child.process.md",
            resource_budgets=[child_budget],
            steps=[ResolvedStep(step_id="leaf", mode="code")],
        ),
        spec=ProcessSpec(name="child"),
        source_path="child.process.md",
    )
    parent = PlanBundle(
        plan=Plan(
            process="parent.process.md",
            steps=[
                ResolvedStep(step_id="first", mode="composite"),
                ResolvedStep(step_id="second", mode="composite"),
            ],
        ),
        spec=ProcessSpec(name="parent"),
        source_path="parent.process.md",
        children={"first": child, "second": child},
    )

    snapshot = build_resource_run_snapshot(parent, run_id="parent/run-1")

    ids = [budget.budget_id for budget in snapshot.budgets]
    assert len(ids) == len(set(ids)) == 2
    assert all(budget.scope.kind is BudgetScopeKind.PROCESS for budget in snapshot.budgets)


def test_snapshot_rejects_duplicate_topology_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate resource topology node ID"):
        ResourceRunSnapshot(
            hierarchy=ResourceTopologyNode(
                node_type="run",
                node_id="run-1",
                label="run-1",
                children=[
                    ResourceTopologyNode(
                        node_type="process",
                        node_id="duplicate",
                        label="one",
                        parent_id="run-1",
                    ),
                    ResourceTopologyNode(
                        node_type="process",
                        node_id="duplicate",
                        label="two",
                        parent_id="run-1",
                    ),
                ],
            )
        )


def test_snapshot_rejects_inconsistent_parent_link() -> None:
    with pytest.raises(ValidationError, match="expected 'run-1'"):
        ResourceRunSnapshot(
            hierarchy=ResourceTopologyNode(
                node_type="run",
                node_id="run-1",
                label="run-1",
                children=[
                    ResourceTopologyNode(
                        node_type="process",
                        node_id="process:root",
                        label="root",
                        parent_id="wrong-parent",
                    )
                ],
            )
        )
