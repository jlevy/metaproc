"""Path computation for steps, items, logs."""

from __future__ import annotations

import glob as glob_mod
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from metaproc.engine.placeholders import resolve_templates
from metaproc.models.authored import ForEach, ProcessSpec, ProcessStep
from metaproc.paths import LOGS_DIR, STATE_DIR, TASKS_SUBDIR, is_safe_item_key
from metaproc.paths import task_logs_dir as _task_logs_dir
from metaproc.paths import task_state_dir as _task_state_dir

_log = logging.getLogger(__name__)


def compute_task_state_dir(
    run_dir: Path,
    step: ProcessStep,
    variables: dict[str, str],
) -> Path:
    """Return the per-task state directory for *step* under *run_dir*.

    For ``for_each`` steps with the loop variable bound in *variables*:
    ``<run_dir>/.state/tasks/<step_id>/<item_key>/`` where ``item_key``
    is resolved from ``for_each.key`` against *variables*. For
    non-fan-out steps, or when called at step-level before item dispatch
    (no loop var bound yet), returns the step-scoped parent
    ``<run_dir>/.state/tasks/<step_id>/`` so boundary-collection code
    can reference the whole step's task subtree.
    """

    step_scoped = run_dir / STATE_DIR / TASKS_SUBDIR / step.id
    if step.for_each is None:
        return step_scoped
    template = step.for_each.key or ("{{" + step.for_each.bind + "}}")
    resolved = resolve_templates(template, variables)
    if "{{" in resolved:
        # Loop variable isn't bound yet — caller is operating at the step
        # level (e.g. building write-boundary targets). Return the parent.
        return step_scoped
    item_key = resolve_item_key(step.for_each, variables, step.id)
    return _task_state_dir(run_dir, step.id, item_key)


def compute_task_logs_dir(
    run_dir: Path,
    step: ProcessStep,
    variables: dict[str, str],
) -> Path:
    """Return the per-task logs directory for *step* under *run_dir*.

    Mirrors :func:`compute_task_state_dir` semantics: per-item path when
    the loop variable is bound, otherwise the step-scoped parent
    ``<run_dir>/.logs/tasks/<step_id>/``.
    """

    step_scoped = run_dir / LOGS_DIR / TASKS_SUBDIR / step.id
    if step.for_each is None:
        return step_scoped
    template = step.for_each.key or ("{{" + step.for_each.bind + "}}")
    resolved = resolve_templates(template, variables)
    if "{{" in resolved:
        return step_scoped
    item_key = resolve_item_key(step.for_each, variables, step.id)
    return _task_logs_dir(run_dir, step.id, item_key)


_KEY_DEFAULT_WARNED: set[str] = set()
"""Dedup set keyed by step_id so the omitted-key warning fires once per step."""


def resolve_item_key(for_each: ForEach, variables: dict[str, str], step_id: str) -> str:
    """Render ``for_each.key`` against *variables* and return the safe item-key.

    An omitted ``key`` defaults to ``"{{<bind>}}"`` (e.g. ``"{{ticker}}"``)
    and emits a once-per-step deprecation warning.

    Raises ``ValueError`` if the template is unresolved at runtime
    (``{{...}}`` still present) or the resolved value is not a safe
    path component.
    """
    template = for_each.key
    if template is None:
        template = "{{" + for_each.bind + "}}"
        if step_id not in _KEY_DEFAULT_WARNED:
            _KEY_DEFAULT_WARNED.add(step_id)
            _log.warning(
                "for_each.key omitted on step %r; defaulting to %r. "
                "Declare an explicit `key:` on the for_each block.",
                step_id,
                template,
            )
    resolved = resolve_templates(template, variables)
    if "{{" in resolved:
        raise ValueError(
            f"for_each.key did not fully resolve for step {step_id!r}: "
            f"template={template!r}, resolved={resolved!r}"
        )
    if not is_safe_item_key(resolved):
        raise ValueError(
            f"for_each.key resolved to an unsafe path component for step "
            f"{step_id!r}: {resolved!r} (allowed: [A-Za-z0-9._-])"
        )
    return resolved


def compute_run_dir(spec: ProcessSpec, variables: dict[str, str]) -> Path:
    """Return the run directory: ``<RUNS_DIR>/<RUN_ID>``.

    When ``RUNS_DIR`` / ``RUN_ID`` are absent from *variables*, the returned
    path contains the literal ``{{run.dir}}`` template token.

    *spec* is retained for signature compatibility and is unused.
    """
    del spec
    return Path(resolve_templates("{{run.dir}}", variables))


def compute_logs_dir(spec: ProcessSpec, variables: dict[str, str]) -> Path:
    """Return the run-level .logs/ directory: ``<RUNS_DIR>/<RUN_ID>/.logs/``."""
    return compute_run_dir(spec, variables) / LOGS_DIR


def common_path(left: Path, right: Path) -> str:
    """Return the common path prefix between two paths."""
    left_parts = left.parts
    right_parts = right.parts
    shared: list[str] = []
    for l_part, r_part in zip(left_parts, right_parts, strict=False):
        if l_part != r_part:
            break
        shared.append(l_part)
    if not shared:
        # Two paths with empty `.parts` (e.g. both `Path('.')`) are EQUAL —
        # their common prefix is themselves. Only raise when the paths are
        # genuinely disjoint.
        if left == right:
            return str(left)
        raise ValueError(f"Paths share no common prefix: {left}, {right}")
    return str(Path(*shared))


def compute_log_filename(step: ProcessStep, variables: dict[str, str]) -> str:
    """Compute the log filename: {step-id}_{context}_{iso-datetime}.jsonl."""
    step_id = step.id

    for_each = step.for_each
    each = for_each.bind if for_each else None
    if each:
        context = variables.get(each, variables.get("DATE", "default"))
    elif "DATE" in variables:
        context = variables["DATE"]
    elif "SCOPE" in variables:
        context = variables["SCOPE"]
    else:
        context = "default"

    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H-%M-%S")
    return f"{step_id}_{context}_{timestamp}.jsonl"


def glob_resolve_path(
    base_dir: Path,
    path_template: str,
    variables: dict[str, str],
) -> Path | None:
    """Resolve a path template, falling back to glob for unresolved vars."""
    resolved = resolve_templates(path_template, variables)
    if "{{" not in resolved:
        candidate = base_dir / resolved
        return candidate if candidate.exists() else None

    glob_pattern = re.sub(r"\{\{\w+\}\}", "*", resolved)
    matches = glob_mod.glob(str(base_dir / glob_pattern))
    if matches:
        return Path(matches[0])
    return None


def find_item_dir(run_dir: Path, item: str) -> Path | None:
    """Locate an item's directory under a run dir."""
    direct = run_dir / item
    if direct.is_dir():
        return direct
    # Fallback: sector-based layout
    matches = glob_mod.glob(str(run_dir / "*" / item))
    if matches and Path(matches[0]).is_dir():
        return Path(matches[0])
    return None
