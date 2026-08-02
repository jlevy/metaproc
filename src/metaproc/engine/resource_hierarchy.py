"""Build a `Node` hierarchy skeleton from a `PlanBundle`.

Walks the recursive `PlanBundle` from `metaproc.models.plan_bundle` and emits the
`run → process → step → item` tree the resource roll-up document
(`metaproc.models.resources.ResourcesDocument`) needs. Reuses the same
qualified node IDs the visualization plane uses by importing from the
neutral ID contract in `metaproc.models.node_ids` — engine code never
reaches into the viz/browser package, so the browser can overlay
resource metrics on the existing Visual tab without coupling the
runtime layer to projection internals.

This module is a *skeleton* builder — every node has zero metrics until
the roll-up engine folds in evidence from logs. Item layer
attachment is optional: callers that have a run directory pass it in to
read `.state/status.yaml` per item; callers that only have the plan
(e.g., a dry-run preview) get just the run/process/step skeleton.
"""

from __future__ import annotations

from pathlib import Path

from metaproc.engine.run_status import scan_step_progress
from metaproc.models.node_ids import (
    ROOT_SUBGRAPH_KEY,
    child_subgraph_key,
    process_node_id,
    step_node_id,
)
from metaproc.models.plan import ResolvedStep
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.resources import Node
from metaproc.models.viz import NodeProgress


def build_hierarchy_skeleton(
    bundle: PlanBundle,
    run_id: str,
    *,
    run_dir: Path | None = None,
) -> Node:
    """Build the run → process → step (→ item) skeleton for a `PlanBundle`.

    ``run_dir`` is optional: when given, item-layer leaves are attached
    by reading `.state/status.yaml` files under each step's run directory
    (composite steps recurse under ``run_dir/step_id``); when omitted,
    only the run/process/step layers are produced.
    """
    return _walk_bundle(
        bundle,
        run_id=run_id,
        subgraph_key=ROOT_SUBGRAPH_KEY,
        run_dir=run_dir,
        parent_id=None,
    )


def _walk_bundle(
    bundle: PlanBundle,
    *,
    run_id: str,
    subgraph_key: str,
    run_dir: Path | None,
    parent_id: str | None,
) -> Node:
    if subgraph_key == ROOT_SUBGRAPH_KEY:
        # Root node represents the whole run; its only child is the root process.
        run_node = Node(
            node_type="run",
            node_id=run_id,
            label=run_id,
            parent_id=None,
        )
        process_node = _build_process_node(
            bundle,
            run_id=run_id,
            subgraph_key=subgraph_key,
            run_dir=run_dir,
            parent_id=run_id,
        )
        run_node.children.append(process_node)
        return run_node

    return _build_process_node(
        bundle,
        run_id=run_id,
        subgraph_key=subgraph_key,
        run_dir=run_dir,
        parent_id=parent_id,
    )


def _build_process_node(
    bundle: PlanBundle,
    *,
    run_id: str,
    subgraph_key: str,
    run_dir: Path | None,
    parent_id: str | None,
) -> Node:
    process_id = process_node_id(subgraph_key)
    label = subgraph_key if subgraph_key == ROOT_SUBGRAPH_KEY else _last_segment(subgraph_key)
    process_node = Node(
        node_type="process",
        node_id=process_id,
        label=label,
        parent_id=parent_id,
    )

    progress: dict[str, NodeProgress] = {}
    if run_dir is not None and run_dir.exists():
        snapshot = scan_step_progress(run_dir, bundle.plan)
        progress = snapshot.nodes

    for step in bundle.plan.steps:
        step_node = _build_step_node(
            step=step,
            subgraph_key=subgraph_key,
            parent_id=process_id,
            run_dir=run_dir,
            progress=progress.get(step.step_id),
        )
        if step.mode == "composite" and step.step_id in bundle.children:
            child_subgraph = child_subgraph_key(subgraph_key, step.step_id)
            child_run_dir = run_dir / step.step_id if run_dir is not None else None
            nested = _build_process_node(
                bundle.children[step.step_id],
                run_id=run_id,
                subgraph_key=child_subgraph,
                run_dir=child_run_dir,
                parent_id=step_node.node_id,
            )
            step_node.children.append(nested)
        process_node.children.append(step_node)

    return process_node


def _build_step_node(
    *,
    step: ResolvedStep,
    subgraph_key: str,
    parent_id: str,
    run_dir: Path | None,
    progress: NodeProgress | None,
) -> Node:
    step_id = step_node_id(subgraph_key, step.step_id)
    step_node = Node(
        node_type="step",
        node_id=step_id,
        label=step.step_id,
        parent_id=parent_id,
    )

    # Composite steps contain nested processes, not items. The caller
    # appends the nested process node; we never attach items here.
    if step.mode == "composite":
        return step_node

    # Item layer comes from per-item status files. The progress scanner
    # already tells us whether to bother enumerating them (no-progress
    # steps short-circuit so dry-run plans don't churn the filesystem).
    if progress is None or progress.total == 0:
        return step_node

    for status_record in _list_item_records(run_dir, step.step_id):
        item_key = _item_record_label(status_record, step.step_id)
        item_node = Node(
            node_type="item",
            node_id=f"{step_id}::{item_key}",
            label=item_key,
            parent_id=step_id,
        )
        step_node.children.append(item_node)

    return step_node


def _list_item_records(run_dir: Path | None, step_id: str) -> list[dict[str, str]]:
    """Enumerate per-item status records for a step.

    Returns a list of ``{"item_key": ...}`` dicts so the caller can
    construct item nodes without knowing the on-disk layout. Returns
    empty when the run dir is missing or the step has no item directory
    structure (scalar steps).
    """
    if run_dir is None:
        return []
    # Per-task state path:
    #   fan-out steps  → <run>/.state/tasks/<step_id>/<item_key>/status.yaml
    #   non-fan-out    → <run>/.state/tasks/<step_id>/status.yaml
    step_state_root = run_dir / ".state" / "tasks" / step_id
    if not step_state_root.exists():
        return []

    records: list[dict[str, str]] = []
    # Non-fan-out: status.yaml directly under the step state root.
    if (step_state_root / "status.yaml").exists():
        records.append({"item_key": ""})
    # Fan-out: one subdir per item_key, each with its own status.yaml.
    for child in sorted(step_state_root.iterdir()):
        if not child.is_dir():
            continue
        if (child / "status.yaml").exists():
            records.append({"item_key": child.name})
    return records


def _item_record_label(record: dict[str, str], step_id: str) -> str:
    """Return a stable item label.

    Scalar steps with one ``.state/status.yaml`` directly under the step dir
    appear as a relative path of ``"."`` — present them as a singleton item
    keyed by the step id so the resulting node is recognisable in tree views.
    """
    item_key = record["item_key"]
    if item_key in ("", "."):
        return step_id
    return item_key


def _last_segment(subgraph_key: str) -> str:
    """Return the trailing segment of a ``parent::child`` subgraph key."""
    if "::" not in subgraph_key:
        return subgraph_key
    return subgraph_key.rsplit("::", 1)[-1]
