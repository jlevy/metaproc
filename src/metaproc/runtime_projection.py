"""Rebuildable task and accepted-output projection for an existing run tree.

The projection reads Metaproc's existing status, attempt, result, run-config, and plan
contracts. It writes no runtime state and is not an execution or lineage authority.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from metaproc.engine.dep_state import fingerprint_step
from metaproc.io import read_yaml_file
from metaproc.io.gz_io import artifact_exists, resolve_existing_artifact
from metaproc.io.state_io import (
    read_result_at,
    read_status_at,
    validate_task_status_identity_at,
)
from metaproc.models.authored import IOSpec
from metaproc.models.plan import ResolvedStep
from metaproc.models.plan_bundle import PlanBundle
from metaproc.models.runtime import ResultRecord, StatusRecord
from metaproc.models.viz import (
    AcceptedOutputProjection,
    OutputRejectionReason,
    ResultBinding,
    RuntimeTaskProjection,
    StepBinding,
    TaskKeyProjection,
    TaskOutputProjection,
    UnacceptedOutputProjection,
)
from metaproc.paths import (
    ATTEMPT_FILE,
    ATTEMPTS_SUBDIR,
    RESULT_FILE,
    STATE_DIR,
    STATUS_FILE,
    TASKS_SUBDIR,
    is_safe_item_key,
    iter_composite_run_dirs,
    run_config_file,
)

_ACCEPTED_STATES = frozenset({"completed", "cached"})


def scan_task_output_projection(run_dir: Path, bundle: PlanBundle) -> TaskOutputProjection:
    """Rebuild task and accepted-output views from one contained run tree.

    Nested composite scopes are discovered from their runtime-owned ``.state``
    branches, then matched back to the corresponding child :class:`PlanBundle`.
    Mutable status is checked against the latest durable attempt before it enters the
    view. Result paths recorded on another host are rebased from the immutable
    ``run-config.yaml`` ``run_dir`` onto *run_dir*.
    """
    current_root = run_dir.resolve()
    original_root, root_run_id, process_name = _read_run_identity(
        run_dir,
        current_root=current_root,
    )
    if process_name is not None and bundle.spec.name != process_name:
        raise ValueError(
            f"{run_config_file(run_dir)}: process {process_name!r} does not match "
            f"loaded plan {bundle.spec.name!r}"
        )
    tasks: list[RuntimeTaskProjection] = []
    seen_state_dirs: set[Path] = set()

    for scope_dir in iter_composite_run_dirs(run_dir):
        scope_path = _scope_path(run_dir, scope_dir)
        scope_bundle = _bundle_for_scope(bundle, scope_path)
        if scope_bundle is None:
            continue
        expected_run_id = _scope_run_id(root_run_id, scope_path)
        for step, item_key, state_dir in _iter_task_state_dirs(
            scope_dir,
            scope_bundle,
            current_root=current_root,
        ):
            resolved_state_dir = state_dir.resolve()
            if resolved_state_dir in seen_state_dirs:
                continue
            seen_state_dirs.add(resolved_state_dir)
            _validate_state_record_containment(state_dir, current_root=current_root)
            status = read_status_at(state_dir)
            if status is None:
                continue
            task_run_id = expected_run_id or status.run_id
            latest_attempt = validate_task_status_identity_at(
                state_dir,
                status,
                run_id=task_run_id,
                step_id=step.step_id,
                item_key=item_key,
            )
            result = read_result_at(state_dir)
            _validate_result_identity(
                state_dir,
                result,
                run_id=task_run_id,
                step_id=step.step_id,
            )
            result_binding = _result_binding(status, result)
            current_step_hash = fingerprint_step(step) if result is not None else None
            step_binding = _step_binding(result, current_step_hash=current_step_hash)
            accepted_outputs, unaccepted_outputs = _project_outputs(
                status,
                result,
                result_binding=result_binding,
                step_binding=step_binding,
                declarations=step.outputs,
                current_root=current_root,
                original_root=original_root,
            )
            tasks.append(
                RuntimeTaskProjection(
                    key=TaskKeyProjection(
                        step_id=step.step_id,
                        item_key=item_key,
                        scope_path=list(scope_path),
                    ),
                    state=status.state,
                    attempt_id=status.attempt_id,
                    attempt_number=status.attempt,
                    generation=status.generation,
                    fence_epoch=status.fence_epoch,
                    attempt_disposition=(
                        latest_attempt.disposition.value
                        if latest_attempt is not None and latest_attempt.disposition is not None
                        else None
                    ),
                    started_at=status.started_at or None,
                    completed_at=status.completed_at,
                    failure_class=status.failure_class,
                    error=status.error,
                    result_binding=result_binding,
                    step_binding=step_binding,
                    accepted_outputs=accepted_outputs,
                    unaccepted_outputs=unaccepted_outputs,
                )
            )

    tasks.sort(
        key=lambda task: (
            tuple(task.key.scope_path),
            task.key.step_id,
            task.key.item_key is not None,
            task.key.item_key or "",
        )
    )
    return TaskOutputProjection(run_dir=str(run_dir), tasks=tasks)


def _read_run_identity(
    run_dir: Path,
    *,
    current_root: Path,
) -> tuple[Path, str | None, str | None]:
    config_path = run_config_file(run_dir)
    if not config_path.exists() and not config_path.is_symlink():
        return run_dir, None, None
    _require_contained_file(config_path, current_root)
    raw = read_yaml_file(config_path)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{config_path}: run config must be a mapping")
    original_raw = raw.get("run_dir")
    if not isinstance(original_raw, str) or not original_raw:
        raise ValueError(f"{config_path}: run_dir must be a non-empty string")
    process_raw = raw.get("process")
    if not isinstance(process_raw, str) or not process_raw:
        raise ValueError(f"{config_path}: process must be a non-empty string")
    run_context_raw = raw.get("run_id")
    if not isinstance(run_context_raw, str):
        raise ValueError(f"{config_path}: run_id must be a string")
    return Path(original_raw), f"{process_raw}/{run_context_raw}", process_raw


def _scope_path(run_dir: Path, scope_dir: Path) -> tuple[str, ...]:
    try:
        return scope_dir.relative_to(run_dir).parts
    except ValueError as exc:
        raise ValueError(f"composite scope {scope_dir} escapes run tree {run_dir}") from exc


def _bundle_for_scope(bundle: PlanBundle, scope_path: tuple[str, ...]) -> PlanBundle | None:
    current = bundle
    index = 0
    while index < len(scope_path):
        composite_id = scope_path[index]
        child = current.children.get(composite_id)
        step = next((entry for entry in current.plan.steps if entry.step_id == composite_id), None)
        if child is None or step is None or step.mode != "composite":
            return None
        index += 1
        if step.fan_out is not None:
            if index >= len(scope_path) or not is_safe_item_key(scope_path[index]):
                return None
            index += 1
        current = child
    return current


def _scope_run_id(root_run_id: str | None, scope_path: tuple[str, ...]) -> str | None:
    if root_run_id is None:
        return None
    return "/".join((root_run_id, *scope_path))


def _iter_task_state_dirs(
    scope_dir: Path,
    bundle: PlanBundle,
    *,
    current_root: Path,
) -> Iterable[tuple[ResolvedStep, str | None, Path]]:
    tasks_root = scope_dir / STATE_DIR / TASKS_SUBDIR
    if not _is_contained_directory(tasks_root, current_root):
        return
    for step in sorted(bundle.plan.steps, key=lambda entry: entry.step_id):
        step_dir = tasks_root / step.step_id
        if not _is_contained_directory(step_dir, current_root):
            continue
        if step.fan_out is None:
            if (step_dir / STATUS_FILE).is_file():
                yield step, None, step_dir
            continue
        try:
            item_dirs = sorted(step_dir.iterdir())
        except OSError:
            continue
        for item_dir in item_dirs:
            if not is_safe_item_key(item_dir.name):
                continue
            if not _is_contained_directory(item_dir, current_root):
                continue
            if (item_dir / STATUS_FILE).is_file():
                yield step, item_dir.name, item_dir


def _is_contained_directory(candidate: Path, root: Path) -> bool:
    try:
        return candidate.is_dir() and candidate.resolve().is_relative_to(root)
    except OSError:
        return False


def _validate_state_record_containment(state_dir: Path, *, current_root: Path) -> None:
    """Refuse state-file symlinks that would make the reader leave the run tree."""
    _require_contained_file(state_dir / STATUS_FILE, current_root)
    result_path = state_dir / RESULT_FILE
    if result_path.exists() or result_path.is_symlink():
        _require_contained_file(result_path, current_root)

    attempts_dir = state_dir / ATTEMPTS_SUBDIR
    if not attempts_dir.exists() and not attempts_dir.is_symlink():
        return
    if not _is_contained_directory(attempts_dir, current_root):
        raise ValueError(f"runtime state path {attempts_dir} escapes run tree {current_root}")
    try:
        attempt_dirs = list(attempts_dir.iterdir())
    except OSError as exc:
        raise ValueError(f"runtime state path {attempts_dir} cannot be read") from exc
    for attempt_dir in attempt_dirs:
        if not attempt_dir.is_dir():
            continue
        if not _is_contained_directory(attempt_dir, current_root):
            raise ValueError(f"runtime state path {attempt_dir} escapes run tree {current_root}")
        attempt_path = attempt_dir / ATTEMPT_FILE
        if attempt_path.exists() or attempt_path.is_symlink():
            _require_contained_file(attempt_path, current_root)


def _require_contained_file(candidate: Path, root: Path) -> None:
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ValueError(f"runtime state path {candidate} cannot be resolved") from exc
    if not candidate.is_file() or not resolved.is_relative_to(root):
        raise ValueError(f"runtime state path {candidate} escapes run tree {root}")


def _validate_result_identity(
    state_dir: Path,
    result: ResultRecord | None,
    *,
    run_id: str,
    step_id: str,
) -> None:
    if result is None:
        return
    mismatches = [
        field_name
        for field_name, recorded, expected in (
            ("run_id", result.run_id, run_id),
            ("step_id", result.step_id, step_id),
        )
        if recorded != expected
    ]
    if mismatches:
        raise ValueError(
            f"{state_dir / RESULT_FILE}: result does not match expected task fields "
            f"{', '.join(mismatches)}"
        )


def _result_binding(
    status: StatusRecord,
    result: ResultRecord | None,
) -> ResultBinding:
    if result is None:
        return "none"
    result_attempt_id = getattr(result, "attempt_id", None)
    if result_attempt_id is None:
        return "legacy-unbound"
    if result_attempt_id != status.attempt_id:
        return "attempt-mismatch"
    return "exact"


def _step_binding(
    result: ResultRecord | None,
    *,
    current_step_hash: str | None,
) -> StepBinding:
    if result is None:
        return "none"
    if not result.step_hash:
        return "legacy-unbound"
    if result.step_hash != current_step_hash:
        return "mismatch"
    return "exact"


def _project_outputs(
    status: StatusRecord,
    result: ResultRecord | None,
    *,
    result_binding: ResultBinding,
    step_binding: StepBinding,
    declarations: Mapping[str, IOSpec],
    current_root: Path,
    original_root: Path,
) -> tuple[list[AcceptedOutputProjection], list[UnacceptedOutputProjection]]:
    if result is None:
        return [], []
    accepted: list[AcceptedOutputProjection] = []
    unaccepted: list[UnacceptedOutputProjection] = []
    for name, recorded_path in sorted(result.outputs.items()):
        declaration = declarations.get(name)
        reason: OutputRejectionReason | None = None
        if status.state not in _ACCEPTED_STATES:
            reason = "task-not-successful"
        elif not result.validated or result.state not in _ACCEPTED_STATES:
            reason = "result-not-validated"
        elif result_binding == "legacy-unbound":
            reason = "legacy-unbound-result"
        elif result_binding == "attempt-mismatch":
            reason = "attempt-mismatch"
        elif step_binding == "legacy-unbound":
            reason = "legacy-unbound-step"
        elif step_binding == "mismatch":
            reason = "step-mismatch"
        elif declaration is None:
            reason = "undeclared"

        portable_path = _rebase_portable_output_path(
            recorded_path,
            current_root=current_root,
            original_root=original_root,
        )
        if reason is None and portable_path is None:
            reason = "external"
        if reason is None and declaration is not None and portable_path is not None:
            available_path, availability_reason = _available_output_path(
                portable_path,
                declaration=declaration,
                current_root=current_root,
            )
            if availability_reason is not None:
                reason = availability_reason
            else:
                accepted.append(
                    AcceptedOutputProjection(
                        name=name,
                        path=str(available_path),
                        recorded_path=recorded_path,
                        declaration=declaration,
                    )
                )
                continue

        unaccepted.append(
            UnacceptedOutputProjection(
                name=name,
                recorded_path=recorded_path,
                reason=reason or "missing",
                path=str(portable_path) if portable_path is not None else None,
                declaration=declaration,
            )
        )
    return accepted, unaccepted


def _rebase_portable_output_path(
    recorded_path: str,
    *,
    current_root: Path,
    original_root: Path,
) -> Path | None:
    """Rebase one run-contained path, or return ``None`` for an external output."""
    if not recorded_path:
        raise ValueError("accepted output path must not be empty")
    recorded = Path(os.path.normpath(recorded_path))
    original = Path(os.path.normpath(str(original_root)))

    if original.is_absolute():
        original_recorded = recorded if recorded.is_absolute() else original / recorded
        try:
            relative = original_recorded.relative_to(original)
        except ValueError:
            return None
    else:
        if recorded.is_absolute():
            return None
        try:
            relative = recorded.relative_to(original)
        except ValueError:
            relative = recorded

    candidate = current_root.joinpath(relative)
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(current_root):
        return None
    return candidate


def _available_output_path(
    candidate: Path,
    *,
    declaration: IOSpec,
    current_root: Path,
) -> tuple[Path, OutputRejectionReason | None]:
    if declaration.kind == "directory":
        if not candidate.exists():
            return candidate, "missing"
        if not candidate.is_dir():
            return candidate, "kind-mismatch"
        return candidate, None

    if candidate.exists() and candidate.is_dir():
        return candidate, "kind-mismatch"
    if not artifact_exists(candidate):
        return candidate, "missing"
    available = resolve_existing_artifact(candidate)
    try:
        resolved = available.resolve()
    except OSError:
        return candidate, "missing"
    if not resolved.is_relative_to(current_root):
        return candidate, "external"
    if not available.is_file():
        return available, "kind-mismatch"
    return available, None


__all__ = ["scan_task_output_projection"]
