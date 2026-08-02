"""Compose neutral plan-bundle loading with runtime progress projection.

Structural loading lives in :mod:`metaproc.plan_bundle_loader` so engine code
never depends on visualization modules. This compatibility surface re-exports
``load_plan_bundle`` and adds progress snapshots for visualization callers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from metaproc.engine.run_status import scan_step_progress
from metaproc.models.node_ids import child_subgraph_key, step_node_id
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.viz import NodeProgress, ProgressSnapshot
from metaproc.plan_bundle_loader import load_plan_bundle as load_plan_bundle


def scan_bundle_progress(run_dir: Path, bundle: PlanBundle) -> ProgressSnapshot:
    """Scan ``run_dir`` recursively and emit one :class:`NodeProgress` per step in ``bundle``.

    Composite steps execute under nested run dirs (``run_dir/step_id/...``),
    so a flat scan at the top level would miss every child-step status.
    This walks the bundle tree, scanning each level's local run dir, and
    keys results by the qualified node id (``step_node_id`` convention) so
    child progress never collides with a root-level step of the same name.
    """
    nodes: dict[str, NodeProgress] = {}
    _walk_bundle_progress(run_dir, bundle, subgraph_key="root", nodes=nodes)
    return ProgressSnapshot(
        run_dir=str(run_dir),
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        nodes=nodes,
    )


def _walk_bundle_progress(
    run_dir: Path,
    bundle: PlanBundle,
    *,
    subgraph_key: str,
    nodes: dict[str, NodeProgress],
) -> None:
    local = scan_step_progress(run_dir, bundle.plan)
    for step in bundle.plan.steps:
        qualified = step_node_id(subgraph_key, step.step_id)
        if step.step_id in local.nodes:
            nodes[qualified] = local.nodes[step.step_id]
        if step.mode == "composite" and step.step_id in bundle.children:
            child_run_dir = run_dir / step.step_id
            child_key = child_subgraph_key(subgraph_key, step.step_id)
            _walk_bundle_progress(
                child_run_dir,
                bundle.children[step.step_id],
                subgraph_key=child_key,
                nodes=nodes,
            )
