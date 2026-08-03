"""Build and read immutable launch-time resource snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from metaproc.engine.resource_hierarchy import build_hierarchy_skeleton
from metaproc.ids import derive_typed_id_from_key
from metaproc.io import read_yaml_file
from metaproc.models.node_ids import (
    ROOT_SUBGRAPH_KEY,
    child_subgraph_key,
    process_node_id,
    step_node_id,
)
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.resource_budget import (
    BudgetScope,
    BudgetScopeKind,
    ResourceBudgetSpec,
    collect_resource_budgets,
)
from metaproc.models.resource_snapshot import (
    RESOURCE_SNAPSHOT_SCHEMA,
    ResourceRunSnapshot,
    ResourceTopologyNode,
)
from metaproc.paths import run_config_file


def build_resource_run_snapshot(bundle: PlanBundle, *, run_id: str) -> ResourceRunSnapshot:
    """Freeze recursive topology and normalized budgets for a new run."""
    hierarchy = build_hierarchy_skeleton(bundle, run_id=run_id)
    budgets = _collect_bundle_budgets(bundle, subgraph_key=ROOT_SUBGRAPH_KEY)
    ids = [budget.budget_id for budget in budgets]
    if len(ids) != len(set(ids)):
        raise ValueError("resource budget IDs must be unique across the recursive plan bundle")
    return ResourceRunSnapshot(
        hierarchy=ResourceTopologyNode.from_node(hierarchy),
        budgets=budgets,
    )


def read_resource_run_snapshot(run_dir: Path) -> ResourceRunSnapshot | None:
    """Read the immutable snapshot, or ``None`` for a legacy run."""
    path = run_config_file(run_dir)
    if not path.exists():
        return None
    raw = read_yaml_file(path)
    if not isinstance(raw, Mapping):
        raise ValueError(f"invalid run config: {path}")
    resources = raw.get("resources")
    if resources is None:
        return None
    if not isinstance(resources, Mapping) or resources.get("schema") != RESOURCE_SNAPSHOT_SCHEMA:
        schema = resources.get("schema") if isinstance(resources, Mapping) else None
        raise ValueError(f"unsupported or missing resource snapshot schema token: {schema!r}")
    return ResourceRunSnapshot.model_validate(resources)


def _collect_bundle_budgets(
    bundle: PlanBundle,
    *,
    subgraph_key: str,
) -> list[ResourceBudgetSpec]:
    budgets = [
        _qualify_budget(budget, bundle=bundle, subgraph_key=subgraph_key)
        for budget in collect_resource_budgets(bundle.plan)
    ]
    for step in bundle.plan.steps:
        child = bundle.children.get(step.step_id)
        if child is None:
            continue
        budgets.extend(
            _collect_bundle_budgets(
                child,
                subgraph_key=child_subgraph_key(subgraph_key, step.step_id),
            )
        )
    return budgets


def _qualify_budget(
    budget: ResourceBudgetSpec,
    *,
    bundle: PlanBundle,
    subgraph_key: str,
) -> ResourceBudgetSpec:
    scope = budget.scope
    qualified = scope
    if scope.kind is BudgetScopeKind.RUN and subgraph_key != ROOT_SUBGRAPH_KEY:
        qualified = BudgetScope(
            kind=BudgetScopeKind.PROCESS,
            key=process_node_id(subgraph_key),
        )
    elif scope.kind is BudgetScopeKind.PROCESS:
        local_names = {bundle.plan.process, bundle.spec.name, process_node_id(subgraph_key)}
        if scope.key in local_names:
            qualified = BudgetScope(
                kind=BudgetScopeKind.PROCESS,
                key=process_node_id(subgraph_key),
            )
    elif scope.kind is BudgetScopeKind.STEP and scope.key is not None:
        local_steps = {step.step_id for step in bundle.plan.steps}
        if scope.key in local_steps:
            qualified = BudgetScope(
                kind=BudgetScopeKind.STEP,
                key=step_node_id(subgraph_key, scope.key),
            )
    budget_id = budget.budget_id
    if subgraph_key != ROOT_SUBGRAPH_KEY:
        budget_id = derive_typed_id_from_key(
            "bud",
            f"{subgraph_key}\x1f{budget.budget_id}",
        )
    return budget.model_copy(
        update={"budget_id": budget_id, "scope": qualified},
        deep=True,
    )
