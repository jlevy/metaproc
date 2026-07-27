"""Compose engine-side loaders into a :class:`PlanBundle` for the projector.

Lives outside ``metaproc.viz`` (which must not depend on the engine) and
outside ``metaproc.engine`` (which must not depend on viz). This module sits
above both and composes them: read a ``.process.md`` file from disk, build the
resolved :class:`Plan`, extract the markdown body, and recurse into composites.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from metaproc.commands.helpers import load_process_spec, resolve_process_path
from metaproc.engine.build_plan import build_plan
from metaproc.engine.process_scope import expand_process_vars
from metaproc.engine.run_status import scan_step_progress
from metaproc.io import fmf_read
from metaproc.models.node_ids import child_subgraph_key, step_node_id
from metaproc.models.viz import NodeProgress, ProgressSnapshot
from metaproc.viz.project import PlanBundle


def load_plan_bundle(
    process_path: Path,
    params: dict[str, str] | None = None,
    *,
    validate_spec: bool = True,
) -> PlanBundle:
    """Load ``process_path`` recursively and return a :class:`PlanBundle`.

    Expands declared process inputs, resolves the plan, reads the body
    markdown, and recurses into every composite step whose resolved
    ``uses_path`` points at another ``.process.md`` file.

    When *validate_spec* is False, spec-level validation (fan-out contracts,
    framework template names, scope collisions, agent write boundaries) is
    skipped so viewers can render structurally-valid-but-semantically-invalid
    specs. See :func:`metaproc.engine.build_plan.build_plan`.
    """
    resolved_path = resolve_process_path(process_path)
    return _load_bundle(resolved_path, dict(params or {}), validate_spec=validate_spec)


def _load_bundle(
    process_path: Path,
    params: dict[str, str],
    *,
    validate_spec: bool,
) -> PlanBundle:
    spec = load_process_spec(process_path)
    expanded = expand_process_vars(spec, params, process_dir=process_path.parent)
    plan = build_plan(
        spec,
        expanded,
        process_path=process_path,
        validate_required_inputs=False,
        validate_spec=validate_spec,
    )
    body = _extract_body_markdown(process_path)
    children: dict[str, PlanBundle] = {}
    for step in plan.steps:
        if step.mode == "composite" and step.uses_path:
            child_path = _resolve_child_path(process_path, step.uses_path)
            if child_path.exists():
                children[step.step_id] = _load_bundle(
                    child_path, dict(step.with_), validate_spec=validate_spec
                )
    return PlanBundle(
        plan=plan,
        spec=spec,
        source_path=str(process_path),
        body_markdown=body,
        children=children,
    )


def _resolve_child_path(parent_path: Path, uses_path: str) -> Path:
    candidate = Path(uses_path)
    if candidate.is_absolute():
        return candidate
    return (parent_path.parent / candidate).resolve()


def _extract_body_markdown(path: Path) -> str:
    """Return the markdown body that follows the frontmatter block."""
    try:
        body, _ = fmf_read(path)
    except (OSError, ValueError):
        return ""
    return body or ""


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
