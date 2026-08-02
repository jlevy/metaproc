"""Load a recursive neutral :class:`PlanBundle` from process specifications."""

from __future__ import annotations

from pathlib import Path

from metaproc.commands.helpers import load_process_spec, resolve_process_path
from metaproc.engine.build_plan import build_plan
from metaproc.engine.process_scope import expand_process_vars
from metaproc.io import fmf_read
from metaproc.models.plan_bundle import PlanBundle


def load_plan_bundle(
    process_path: Path,
    params: dict[str, str] | None = None,
    *,
    validate_spec: bool = True,
) -> PlanBundle:
    """Resolve a process and all available composite children into one bundle."""
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
    children: dict[str, PlanBundle] = {}
    for step in plan.steps:
        if step.mode != "composite" or not step.uses_path:
            continue
        child_path = _resolve_child_path(process_path, step.uses_path)
        if child_path.exists():
            children[step.step_id] = _load_bundle(
                child_path,
                dict(step.with_),
                validate_spec=validate_spec,
            )
    return PlanBundle(
        plan=plan,
        spec=spec,
        source_path=str(process_path),
        body_markdown=_extract_body_markdown(process_path),
        children=children,
    )


def _resolve_child_path(parent_path: Path, uses_path: str) -> Path:
    candidate = Path(uses_path)
    if candidate.is_absolute():
        return candidate
    return (parent_path.parent / candidate).resolve()


def _extract_body_markdown(path: Path) -> str:
    try:
        body, _ = fmf_read(path)
    except (OSError, ValueError):
        return ""
    return body or ""
