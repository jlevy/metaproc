"""Atomic I/O for .state/ runtime records.

Provides read/write functions for status, latest-launch, durable attempt-history, and
result records. All writes are atomic (write to temp, then rename).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from frontmatter_format import read_yaml_file, to_yaml_string
from strif import atomic_output_file

from metaproc.engine.placeholders import resolve_templates
from metaproc.errors import AttemptTerminalConflictError
from metaproc.ids import new_timestamped_typed_id, require_typed_id
from metaproc.models.authored import IOSpec
from metaproc.models.plan import RunPlanSnapshot
from metaproc.models.runtime import (
    AttemptDisposition,
    AttemptRecord,
    ManualAckRecord,
    OutputFailure,
    ResultRecord,
    StatusRecord,
    TaskAttemptRecord,
)
from metaproc.paths import (
    ATTEMPT_FILE,
    ATTEMPTS_SUBDIR,
    MANUAL_ACK_FILE,
    POOL_STATUS_FILE,
    RESULT_FILE,
    RUN_PLAN_FILE,
    STATE_DIR,
    STATUS_FILE,
    TASKS_SUBDIR,
    attempt_state_dir,
)
from metaproc.paths import step_state_dir as _step_state_dir
from metaproc.runpool.status import RunPoolStatus, is_pool_alive, read_status

log = logging.getLogger(__name__)


def _write_record_at(state_dir: Path, filename: str, data: dict[str, object]) -> Path:
    """Write a YAML record atomically to ``state_dir/filename``.

    *state_dir* is the directory the state file lives in directly — there
    is no inner ``.state/`` subdir.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / filename
    with atomic_output_file(target) as tmp_path:
        Path(tmp_path).write_text(to_yaml_string(data), encoding="utf-8")
    return target


# ── New direct (state_dir-based) write/read API ─────────────────


def write_status_at(state_dir: Path, record: StatusRecord) -> Path:
    # ``mode="json"`` because the destination is a YAML document, not Python.
    # ``OutputFailure.kind`` is a StrEnum, and a Python-mode dump hands the
    # enum member itself to the YAML writer, which cannot represent it — the
    # first output failure carrying a structured record would raise instead of
    # being recorded. Every other field serializes identically either way.
    return _write_record_at(state_dir, STATUS_FILE, record.model_dump(mode="json"))


def write_attempt_at(state_dir: Path, record: AttemptRecord) -> Path:
    return _write_record_at(state_dir, ATTEMPT_FILE, record.model_dump(exclude_none=True))


def write_result_at(state_dir: Path, record: ResultRecord) -> Path:
    validate_result_attempt_identity_at(state_dir, record)
    return _write_record_at(state_dir, RESULT_FILE, record.model_dump(exclude_none=True))


def write_manual_ack_at(state_dir: Path, record: ManualAckRecord) -> Path:
    return _write_record_at(state_dir, MANUAL_ACK_FILE, record.model_dump())


def write_run_plan(run_dir: Path, record: RunPlanSnapshot) -> Path:
    """Publish the exact non-sensitive plan projection for one process scope."""
    return _write_record_at(
        run_dir / STATE_DIR,
        RUN_PLAN_FILE,
        {"run_plan": record.model_dump(mode="json", by_alias=True)},
    )


def read_status_at(state_dir: Path) -> StatusRecord | None:
    """Read ``state_dir/status.yaml`` directly. Returns None if absent."""
    status_path = state_dir / STATUS_FILE
    if not status_path.exists():
        return None
    raw: object = read_yaml_file(status_path)
    return StatusRecord.model_validate(raw)


def read_run_plan(run_dir: Path) -> RunPlanSnapshot | None:
    """Read one scope's resolved plan snapshot when the run recorded it."""
    path = run_dir / STATE_DIR / RUN_PLAN_FILE
    if not path.exists():
        return None
    raw: object = read_yaml_file(path)
    if not isinstance(raw, dict) or "run_plan" not in raw:
        raise ValueError(f"{path}: expected a run_plan envelope")
    return RunPlanSnapshot.model_validate(raw["run_plan"])


def read_attempt_at(state_dir: Path) -> AttemptRecord | None:
    attempt_path = state_dir / ATTEMPT_FILE
    if not attempt_path.exists():
        return None
    raw: object = read_yaml_file(attempt_path)
    return AttemptRecord.model_validate(raw)


def read_task_attempt_at(state_dir: Path, attempt_id: str) -> TaskAttemptRecord | None:
    """Read one durable attempt record, or return ``None`` when it is absent."""
    require_typed_id(attempt_id, "att")
    attempt_path = attempt_state_dir(state_dir, attempt_id) / ATTEMPT_FILE
    if not attempt_path.exists():
        return None
    raw: object = read_yaml_file(attempt_path)
    record = TaskAttemptRecord.model_validate(raw)
    require_typed_id(record.attempt_id, "att")
    if record.attempt_id != attempt_id:
        raise ValueError(
            f"attempt directory {attempt_id!r} contains record for {record.attempt_id!r}"
        )
    return record


def read_attempt_history_at(state_dir: Path) -> tuple[TaskAttemptRecord, ...]:
    """Read every retained attempt for a task in deterministic launch order."""
    attempts_dir = state_dir / ATTEMPTS_SUBDIR
    if not attempts_dir.is_dir():
        return ()
    records: list[TaskAttemptRecord] = []
    for child in attempts_dir.iterdir():
        if not child.is_dir():
            continue
        record = read_task_attempt_at(state_dir, child.name)
        if record is None:
            raise ValueError(f"{child}: missing {ATTEMPT_FILE}")
        records.append(record)
    ordered = sorted(records, key=lambda record: (record.attempt_number, record.attempt_id))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.attempt_number == current.attempt_number:
            raise ValueError(
                f"{state_dir}: duplicate attempt_number {current.attempt_number} in "
                f"{previous.attempt_id!r} and {current.attempt_id!r}"
            )
    return tuple(ordered)


def validate_result_attempt_identity_at(
    state_dir: Path,
    result: ResultRecord,
) -> TaskAttemptRecord | None:
    """Require a result to name the latest successful durable attempt, when present."""
    history = read_attempt_history_at(state_dir)
    if not history:
        if result.attempt_id is not None:
            raise ValueError(f"{state_dir}: result names missing attempt {result.attempt_id!r}")
        return None

    latest = history[-1]
    if result.attempt_id != latest.attempt_id:
        raise ValueError(f"{state_dir}: result does not name latest attempt {latest.attempt_id!r}")
    mismatches = [
        field_name
        for field_name, result_value, attempt_value in (
            ("run_id", result.run_id, latest.run_id),
            ("step_id", result.step_id, latest.step_id),
        )
        if result_value != attempt_value
    ]
    if mismatches:
        raise ValueError(
            f"{state_dir}: result does not match latest attempt fields {', '.join(mismatches)}"
        )
    if latest.disposition is not AttemptDisposition.succeeded:
        raise ValueError(
            f"{state_dir}: result points to {latest.disposition or 'live'} attempt "
            f"{latest.attempt_id!r}"
        )
    return latest


def _write_task_attempt_at(state_dir: Path, record: TaskAttemptRecord) -> Path:
    return _write_record_at(
        attempt_state_dir(state_dir, record.attempt_id),
        ATTEMPT_FILE,
        record.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


def start_attempt_at(
    state_dir: Path,
    *,
    run_id: str,
    step_id: str,
    item: dict[str, str],
    attempt_number: int | None = None,
    item_key: str | None = None,
    scope_path: tuple[str, ...] = (),
    generation: int = 1,
    fence_epoch: int = 0,
) -> TaskAttemptRecord:
    """Persist an attempt before launch and return its immutable identity."""
    history = read_attempt_history_at(state_dir)
    requested_scope = list(scope_path)
    for existing in history:
        mismatches = [
            field_name
            for field_name, existing_value, requested_value in (
                ("run_id", existing.run_id, run_id),
                ("step_id", existing.step_id, step_id),
                ("item_key", existing.item_key, item_key),
                ("scope_path", existing.scope_path, requested_scope),
            )
            if existing_value != requested_value
        ]
        if mismatches:
            raise ValueError(
                f"{state_dir}: attempt {existing.attempt_id!r} does not match "
                f"requested task fields {', '.join(mismatches)}"
            )
        if existing.disposition is None:
            raise ValueError(
                f"{state_dir}: attempt {existing.attempt_id!r} is still live; "
                "reconcile or finalize it before starting another"
            )
    next_number = max((record.attempt_number for record in history), default=0) + 1
    number = max(next_number, attempt_number or 1)
    task_item = history[0].item if history else item
    record = TaskAttemptRecord(
        attempt_id=new_timestamped_typed_id("att"),
        run_id=run_id,
        step_id=step_id,
        item_key=item_key,
        item=task_item,
        scope_path=requested_scope,
        generation=generation,
        fence_epoch=fence_epoch,
        attempt_number=number,
        started_at=_now_iso(),
    )
    _write_task_attempt_at(state_dir, record)
    return record


def end_attempt_at(
    state_dir: Path,
    *,
    attempt_id: str,
    disposition: AttemptDisposition,
    failure_class: str | None = None,
    error: str | None = None,
    output_failures: Sequence[OutputFailure] | None = None,
    anomalies: Sequence[str] | None = None,
) -> TaskAttemptRecord:
    """Finalize one attempt exactly once."""
    current = read_task_attempt_at(state_dir, attempt_id)
    if current is None:
        raise ValueError(f"{state_dir}: attempt {attempt_id!r} does not exist")
    if current.disposition is not None:
        same_terminal_fact = (
            current.disposition is disposition
            and current.failure_class == failure_class
            and current.error == error
            and current.output_failures == list(output_failures or [])
            and current.anomalies == list(anomalies or [])
        )
        if same_terminal_fact:
            return current
        raise AttemptTerminalConflictError(
            f"attempt {attempt_id!r} already has a different terminal fact"
        )
    terminal = TaskAttemptRecord.model_validate(
        {
            **current.model_dump(mode="python", by_alias=True),
            "disposition": disposition,
            "ended_at": _now_iso(),
            "failure_class": failure_class,
            "error": error,
            "output_failures": list(output_failures or []),
            "anomalies": list(anomalies or []),
        }
    )
    _write_task_attempt_at(state_dir, terminal)
    return terminal


def end_status_attempt_at(
    state_dir: Path,
    status: StatusRecord,
    *,
    disposition: AttemptDisposition,
    failure_class: str | None = None,
    error: str | None = None,
    output_failures: Sequence[OutputFailure] | None = None,
    anomalies: Sequence[str] | None = None,
) -> TaskAttemptRecord | None:
    """Finalize the durable attempt named by a status record, if it has one."""
    if status.attempt_id is None:
        return None
    current = read_task_attempt_at(state_dir, status.attempt_id)
    if current is None:
        raise ValueError(f"{state_dir}: status names missing attempt {status.attempt_id!r}")
    _validate_attempt_matches_status(state_dir, current, status)
    return end_attempt_at(
        state_dir,
        attempt_id=status.attempt_id,
        disposition=disposition,
        failure_class=failure_class,
        error=error,
        output_failures=output_failures,
        anomalies=anomalies,
    )


def _validate_attempt_matches_status(
    state_dir: Path,
    attempt: TaskAttemptRecord,
    status: StatusRecord,
) -> None:
    mismatches: list[str] = []
    for field_name, attempt_value, status_value in (
        ("run_id", attempt.run_id, status.run_id),
        ("step_id", attempt.step_id, status.step_id),
        ("item", attempt.item, status.item),
        ("attempt_number", attempt.attempt_number, status.attempt),
        ("generation", attempt.generation, status.generation),
        ("fence_epoch", attempt.fence_epoch, status.fence_epoch),
    ):
        if attempt_value != status_value:
            mismatches.append(field_name)
    if mismatches:
        raise ValueError(
            f"{state_dir}: attempt {attempt.attempt_id!r} does not match status fields "
            f"{', '.join(mismatches)}"
        )


def validate_task_status_identity_at(
    state_dir: Path,
    status: StatusRecord,
    *,
    run_id: str,
    step_id: str,
    item_key: str | None,
) -> TaskAttemptRecord | None:
    """Validate a mutable task projection and return its latest durable attempt."""
    mismatches = [
        field_name
        for field_name, recorded_value, expected_value in (
            ("run_id", status.run_id, run_id),
            ("step_id", status.step_id, step_id),
        )
        if recorded_value != expected_value
    ]
    if mismatches:
        raise ValueError(
            f"{state_dir}: status does not match expected task fields {', '.join(mismatches)}"
        )

    history = read_attempt_history_at(state_dir)
    if not history:
        if status.attempt_id is not None:
            raise ValueError(f"{state_dir}: status names missing attempt {status.attempt_id!r}")
        return None

    for attempt in history:
        attempt_mismatches = [
            field_name
            for field_name, recorded_value, expected_value in (
                ("run_id", attempt.run_id, run_id),
                ("step_id", attempt.step_id, step_id),
                ("item_key", attempt.item_key, item_key),
            )
            if recorded_value != expected_value
        ]
        if attempt_mismatches:
            raise ValueError(
                f"{state_dir}: attempt {attempt.attempt_id!r} does not match expected "
                f"task fields {', '.join(attempt_mismatches)}"
            )

    latest = history[-1]
    if status.attempt_id != latest.attempt_id:
        raise ValueError(f"{state_dir}: status does not name latest attempt {latest.attempt_id!r}")
    _validate_attempt_matches_status(state_dir, latest, status)
    if status.state in {"completed", "cached"} and (
        latest.disposition is not AttemptDisposition.succeeded
    ):
        raise ValueError(
            f"{state_dir}: successful status points to {latest.disposition or 'live'} attempt "
            f"{latest.attempt_id!r}"
        )
    if status.state == "failed" and latest.disposition is AttemptDisposition.succeeded:
        raise ValueError(
            f"{state_dir}: failed status points to succeeded attempt {latest.attempt_id!r}"
        )
    return latest


def _terminal_status_from_attempt(attempt: TaskAttemptRecord) -> StatusRecord:
    """Build the canonical mutable projection of a terminal attempt."""
    if attempt.disposition is None or attempt.ended_at is None:
        raise ValueError(f"attempt {attempt.attempt_id!r} is not terminal")
    common: dict[str, object] = {
        "run_id": attempt.run_id,
        "step_id": attempt.step_id,
        "item": attempt.item,
        "attempt": attempt.attempt_number,
        "attempt_id": attempt.attempt_id,
        "generation": attempt.generation,
        "fence_epoch": attempt.fence_epoch,
        "started_at": attempt.started_at,
        "completed_at": attempt.ended_at,
    }
    if attempt.disposition is AttemptDisposition.succeeded:
        return StatusRecord(state="completed", **common)  # pyright: ignore[reportArgumentType]
    return StatusRecord(
        state="failed",
        error=attempt.error or f"attempt ended as {attempt.disposition.value}",
        failure_class=attempt.failure_class,
        output_failures=attempt.output_failures,
        **common,  # pyright: ignore[reportArgumentType]
    )


def _project_terminal_attempt_at(
    state_dir: Path,
    attempt: TaskAttemptRecord,
) -> StatusRecord:
    """Rebuild the mutable status projection from an accepted terminal attempt."""
    projected = _terminal_status_from_attempt(attempt)
    write_status_at(state_dir, projected)
    return projected


def read_result_at(state_dir: Path) -> ResultRecord | None:
    result_path = state_dir / RESULT_FILE
    if not result_path.exists():
        return None
    raw: object = read_yaml_file(result_path)
    return ResultRecord.model_validate(raw)


def read_manual_ack_at(state_dir: Path) -> ManualAckRecord | None:
    ack_path = state_dir / MANUAL_ACK_FILE
    if not ack_path.exists():
        return None
    raw: object = read_yaml_file(ack_path)
    return ManualAckRecord.model_validate(raw)


# ── Harness transition helpers ───────────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _validate_status_before_attempt_start(
    state_dir: Path,
    *,
    run_id: str,
    step_id: str,
) -> None:
    current_status = read_status_at(state_dir)
    history = read_attempt_history_at(state_dir)
    status_for_history = current_status
    resuming_invalidated_task = False
    if current_status is None and history:
        stale_status_path = (state_dir / STATUS_FILE).with_suffix(".yaml.stale")
        if stale_status_path.exists():
            status_for_history = StatusRecord.model_validate(read_yaml_file(stale_status_path))
            resuming_invalidated_task = True

    if status_for_history is not None:
        mismatches = [
            field_name
            for field_name, existing_value, requested_value in (
                ("run_id", status_for_history.run_id, run_id),
                ("step_id", status_for_history.step_id, step_id),
            )
            if existing_value != requested_value
        ]
        if mismatches:
            raise ValueError(
                f"{state_dir}: existing status does not match requested fields "
                f"{', '.join(mismatches)}"
            )
    if history:
        latest = history[-1]
        if status_for_history is None or status_for_history.attempt_id != latest.attempt_id:
            raise ValueError(
                f"{state_dir}: status does not name latest attempt {latest.attempt_id!r}; "
                "reconcile before starting another"
            )
        _validate_attempt_matches_status(state_dir, latest, status_for_history)
        if resuming_invalidated_task and latest.disposition is None:
            raise ValueError(f"{state_dir}: cannot invalidate live attempt {latest.attempt_id!r}")
    elif current_status is not None and current_status.attempt_id is not None:
        raise ValueError(f"{state_dir}: status names missing attempt {current_status.attempt_id!r}")


def mark_running_at(
    state_dir: Path,
    *,
    run_id: str,
    step_id: str,
    item: dict[str, str],
    attempt: int | None = None,
    item_key: str | None = None,
    scope_path: tuple[str, ...] = (),
    generation: int = 1,
    fence_epoch: int = 0,
) -> StatusRecord:
    """Write ``state_dir/status.yaml`` with state=running before launching agent."""
    _validate_status_before_attempt_start(
        state_dir,
        run_id=run_id,
        step_id=step_id,
    )
    durable_attempt = start_attempt_at(
        state_dir,
        run_id=run_id,
        step_id=step_id,
        item=item,
        attempt_number=attempt,
        item_key=item_key,
        scope_path=scope_path,
        generation=generation,
        fence_epoch=fence_epoch,
    )
    record = StatusRecord(
        run_id=run_id,
        step_id=step_id,
        item=durable_attempt.item,
        state="running",
        attempt=durable_attempt.attempt_number,
        attempt_id=durable_attempt.attempt_id,
        generation=generation,
        fence_epoch=fence_epoch,
        started_at=durable_attempt.started_at,
    )
    write_status_at(state_dir, record)
    return record


def _read_or_use_at(state_dir: Path, running_record: StatusRecord | None) -> StatusRecord:
    if running_record is not None:
        return running_record
    current = read_status_at(state_dir)
    if current is None:
        msg = f"{state_dir}: no status.yaml to transition"
        raise ValueError(msg)
    return current


def mark_completed_at(
    state_dir: Path,
    *,
    running_record: StatusRecord | None = None,
    anomalies: Sequence[str] | None = None,
) -> StatusRecord:
    """Transition ``state_dir/status.yaml`` from running to completed.

    *anomalies* records irregularities the harness accepted on the way to this success,
    so a step that passed only because a rule was relaxed says so in its attempt history
    rather than only in progress output.
    """
    current = _read_or_use_at(state_dir, running_record)
    record = StatusRecord(
        run_id=current.run_id,
        step_id=current.step_id,
        item=current.item,
        state="completed",
        attempt=current.attempt,
        attempt_id=current.attempt_id,
        generation=current.generation,
        fence_epoch=current.fence_epoch,
        started_at=current.started_at,
        completed_at=_now_iso(),
    )
    end_status_attempt_at(
        state_dir, current, disposition=AttemptDisposition.succeeded, anomalies=anomalies
    )
    write_status_at(state_dir, record)
    return record


def mark_failed_at(
    state_dir: Path,
    *,
    error: str,
    running_record: StatusRecord | None = None,
    output_failures: Sequence[OutputFailure] | None = None,
    failure_class: str | None = None,
    attempt_disposition: AttemptDisposition | None = AttemptDisposition.permanent,
) -> StatusRecord:
    """Transition ``state_dir/status.yaml`` from running to failed.

    ``output_failures`` records which invariant refused which declared output,
    so a reader does not have to recover that from ``error``.
    """
    current = _read_or_use_at(state_dir, running_record)
    record = StatusRecord(
        run_id=current.run_id,
        step_id=current.step_id,
        item=current.item,
        state="failed",
        attempt=current.attempt,
        attempt_id=current.attempt_id,
        generation=current.generation,
        fence_epoch=current.fence_epoch,
        started_at=current.started_at,
        completed_at=_now_iso(),
        error=error,
        failure_class=failure_class,
        output_failures=list(output_failures or []),
    )
    if attempt_disposition is not None:
        end_status_attempt_at(
            state_dir,
            current,
            disposition=attempt_disposition,
            failure_class=failure_class,
            error=error,
            output_failures=output_failures,
        )
    write_status_at(state_dir, record)
    return record


def mark_failed_synthetic_at(
    state_dir: Path,
    *,
    run_id: str,
    step_id: str,
    item: dict[str, str],
    error: str,
    attempt: int = 1,
    item_key: str | None = None,
    scope_path: tuple[str, ...] = (),
    generation: int = 1,
    fence_epoch: int = 0,
    attempt_disposition: AttemptDisposition | None = AttemptDisposition.lost,
) -> StatusRecord:
    """Write failed status when launch failed before running status existed."""
    _validate_status_before_attempt_start(
        state_dir,
        run_id=run_id,
        step_id=step_id,
    )
    durable_attempt = start_attempt_at(
        state_dir,
        run_id=run_id,
        step_id=step_id,
        item=item,
        attempt_number=attempt,
        item_key=item_key,
        scope_path=scope_path,
        generation=generation,
        fence_epoch=fence_epoch,
    )
    record = StatusRecord(
        run_id=run_id,
        step_id=step_id,
        item=durable_attempt.item,
        state="failed",
        attempt=durable_attempt.attempt_number,
        attempt_id=durable_attempt.attempt_id,
        generation=durable_attempt.generation,
        fence_epoch=durable_attempt.fence_epoch,
        started_at=durable_attempt.started_at,
        completed_at=durable_attempt.started_at,
        error=error,
    )
    if attempt_disposition is not None:
        end_attempt_at(
            state_dir,
            attempt_id=durable_attempt.attempt_id,
            disposition=attempt_disposition,
            error=error,
        )
    write_status_at(state_dir, record)
    return record


def compute_item_dir(
    output_paths: dict[str, IOSpec],
    variables: dict[str, str],
) -> Path | None:
    """Compute item directory from resolved output path templates.

    For ``kind: directory`` outputs, the resolved path IS the item directory.
    For file outputs, the parent directory is the item directory.
    """
    for io_spec in output_paths.values():
        if not io_spec.path:
            continue
        resolved = resolve_templates(io_spec.path, variables)
        path = Path(resolved)
        if io_spec.kind == "directory":
            return path
        return path.parent
    return None


# ── Stale-running reconciliation ────────────────────────────────


def _find_pool_status(run_dir: Path, step_id: str | None = None) -> RunPoolStatus | None:
    """Locate and read the pool status file for a run directory.

    Per-step pool status lives at ``<run_dir>/.state/steps/<step_id>/runpool-status.yaml``;
    the run-level pool (for run-parallel) lives at ``<run_dir>/.state/runpool-status.yaml``.
    """

    if step_id is not None:
        step_status = _step_state_dir(run_dir, step_id) / POOL_STATUS_FILE
        if step_status.exists():
            return read_status(step_status)

    status_path = run_dir / STATE_DIR / POOL_STATUS_FILE
    if not status_path.exists():
        return None
    return read_status(status_path)


def _task_address(tasks_root: Path, state_dir: Path) -> tuple[str, str | None]:
    parts = state_dir.relative_to(tasks_root).parts
    if len(parts) == 1:
        return parts[0], None
    if len(parts) == 2:
        return parts[0], parts[1]
    raise ValueError(f"{state_dir}: invalid task-state path")


def _validate_attempt_matches_task_address(
    state_dir: Path,
    attempt: TaskAttemptRecord,
    *,
    step_id: str,
    item_key: str | None,
) -> None:
    if attempt.step_id != step_id or attempt.item_key != item_key:
        raise ValueError(
            f"{state_dir}: attempt {attempt.attempt_id!r} identifies "
            f"{attempt.step_id}[{attempt.item_key}] instead of {step_id}[{item_key}]"
        )


def reconcile_stale_running(run_dir: Path) -> int:
    """Reconcile orphaned attempts and mutable task-status projections.

    A live pool owns its attempts. Once the relevant run-level or step-level pool is
    dead, any retained live attempt becomes ``lost``. The latest terminal attempt is
    then projected back into ``status.yaml``. Scanning attempt directories as well as
    status files covers both cross-file crash windows: attempt creation before status
    and attempt finalization before terminal status. Returns the number of tasks whose
    durable facts or status projection changed.
    """
    run_pool_status = _find_pool_status(run_dir)
    if run_pool_status is not None and is_pool_alive(run_pool_status):
        return 0

    tasks_root = run_dir / STATE_DIR / TASKS_SUBDIR
    if not tasks_root.exists():
        return 0

    task_dirs = {path.parent for path in tasks_root.rglob(STATUS_FILE)}
    task_dirs.update(path.parent for path in tasks_root.rglob(ATTEMPTS_SUBDIR) if path.is_dir())

    reset_count = 0
    step_pool_statuses: dict[str, RunPoolStatus | None] = {}
    for state_dir in sorted(task_dirs):
        step_id, item_key = _task_address(tasks_root, state_dir)
        if step_id not in step_pool_statuses:
            step_pool_statuses[step_id] = _find_pool_status(run_dir, step_id)
        step_pool_status = step_pool_statuses[step_id]
        if step_pool_status is not None and is_pool_alive(step_pool_status):
            continue

        record = read_status_at(state_dir)
        history = list(read_attempt_history_at(state_dir))
        if not history:
            if record is None or record.state != "running":
                continue
            mark_failed_at(
                state_dir,
                running_record=record,
                error="orphaned: pool process died while item was running",
                attempt_disposition=AttemptDisposition.lost,
            )
            reset_count += 1
            continue

        for attempt in history:
            _validate_attempt_matches_task_address(
                state_dir,
                attempt,
                step_id=step_id,
                item_key=item_key,
            )
        run_ids = {attempt.run_id for attempt in history}
        if len(run_ids) != 1:
            raise ValueError(f"{state_dir}: attempt history spans multiple run_id values")

        changed = False
        for index, attempt in enumerate(history[:-1]):
            if attempt.disposition is not None:
                continue
            history[index] = end_attempt_at(
                state_dir,
                attempt_id=attempt.attempt_id,
                disposition=AttemptDisposition.lost,
                error="orphaned: a later attempt exists without this attempt ending",
            )
            changed = True

        latest = history[-1]
        status_names_latest = record is not None and record.attempt_id == latest.attempt_id
        if record is not None and status_names_latest:
            _validate_attempt_matches_status(state_dir, latest, record)
        elif record is not None and (
            latest.run_id != record.run_id
            or latest.step_id != record.step_id
            or latest.item != record.item
        ):
            raise ValueError(
                f"{state_dir}: latest attempt {latest.attempt_id!r} does not match prior status"
            )

        if latest.disposition is None:
            orphan_error = (
                "orphaned: pool process died while item was running"
                if status_names_latest
                else "orphaned: attempt persisted before status projection"
            )
            latest = end_attempt_at(
                state_dir,
                attempt_id=latest.attempt_id,
                disposition=AttemptDisposition.lost,
                error=orphan_error,
            )
            changed = True

        expected_status = _terminal_status_from_attempt(latest)
        if record != expected_status:
            write_status_at(state_dir, expected_status)
            changed = True

        if not changed:
            continue
        reset_count += 1
        log.info(
            "Reconciled orphaned task: %s (run_id=%s, attempt=%d)",
            state_dir,
            latest.run_id,
            latest.attempt_number,
        )

    if reset_count:
        log.info("Reconciled %d orphaned task(s) in %s", reset_count, run_dir)
    return reset_count
