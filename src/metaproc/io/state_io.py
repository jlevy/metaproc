"""Atomic I/O for .state/ runtime records.

Provides read/write functions for StatusRecord, AttemptRecord, and
ResultRecord. All writes are atomic (write to temp, then rename).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from frontmatter_format import read_yaml_file, to_yaml_string
from pydantic import ValidationError as PydanticValidationError
from strif import atomic_output_file

from metaproc.engine.placeholders import resolve_templates
from metaproc.models.authored import IOSpec
from metaproc.models.runtime import AttemptRecord, ManualAckRecord, ResultRecord, StatusRecord
from metaproc.paths import (
    ATTEMPT_FILE,
    MANUAL_ACK_FILE,
    POOL_STATUS_FILE,
    RESULT_FILE,
    STATE_DIR,
    STATUS_FILE,
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
    return _write_record_at(state_dir, STATUS_FILE, record.model_dump())


def write_attempt_at(state_dir: Path, record: AttemptRecord) -> Path:
    return _write_record_at(state_dir, ATTEMPT_FILE, record.model_dump(exclude_none=True))


def write_result_at(state_dir: Path, record: ResultRecord) -> Path:
    return _write_record_at(state_dir, RESULT_FILE, record.model_dump(exclude_none=True))


def write_manual_ack_at(state_dir: Path, record: ManualAckRecord) -> Path:
    return _write_record_at(state_dir, MANUAL_ACK_FILE, record.model_dump())


def read_status_at(state_dir: Path) -> StatusRecord | None:
    """Read ``state_dir/status.yaml`` directly. Returns None if absent."""
    status_path = state_dir / STATUS_FILE
    if not status_path.exists():
        return None
    raw: object = read_yaml_file(status_path)
    return StatusRecord.model_validate(raw)


def read_attempt_at(state_dir: Path) -> AttemptRecord | None:
    attempt_path = state_dir / ATTEMPT_FILE
    if not attempt_path.exists():
        return None
    raw: object = read_yaml_file(attempt_path)
    return AttemptRecord.model_validate(raw)


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


def mark_running_at(
    state_dir: Path,
    *,
    run_id: str,
    step_id: str,
    item: dict[str, str],
    attempt: int = 1,
) -> StatusRecord:
    """Write ``state_dir/status.yaml`` with state=running before launching agent."""
    record = StatusRecord(
        run_id=run_id,
        step_id=step_id,
        item=item,
        state="running",
        attempt=attempt,
        started_at=_now_iso(),
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
) -> StatusRecord:
    """Transition ``state_dir/status.yaml`` from running to completed."""
    current = _read_or_use_at(state_dir, running_record)
    record = StatusRecord(
        run_id=current.run_id,
        step_id=current.step_id,
        item=current.item,
        state="completed",
        attempt=current.attempt,
        started_at=current.started_at,
        completed_at=_now_iso(),
    )
    write_status_at(state_dir, record)
    return record


def mark_failed_at(
    state_dir: Path,
    *,
    error: str,
    running_record: StatusRecord | None = None,
) -> StatusRecord:
    """Transition ``state_dir/status.yaml`` from running to failed."""
    current = _read_or_use_at(state_dir, running_record)
    record = StatusRecord(
        run_id=current.run_id,
        step_id=current.step_id,
        item=current.item,
        state="failed",
        attempt=current.attempt,
        started_at=current.started_at,
        completed_at=_now_iso(),
        error=error,
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
) -> StatusRecord:
    """Write failed status when launch failed before running status existed."""
    now = _now_iso()
    record = StatusRecord(
        run_id=run_id,
        step_id=step_id,
        item=item,
        state="failed",
        attempt=attempt,
        started_at=now,
        completed_at=now,
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


def reconcile_stale_running(run_dir: Path) -> int:
    """Reset orphaned 'running' items whose pool is no longer alive.

    Scans per-task status files at ``<run>/.state/tasks/<step>/<item>/status.yaml``
    (and ``<run>/.state/tasks/<step>/status.yaml`` for non-fan-out steps).
    Items with ``state: running`` are transitioned to ``state: failed`` when
    the pool that last managed this run directory is dead (or no pool status
    file exists). Returns the number of items reset.
    """
    pool_status = _find_pool_status(run_dir)
    if pool_status is not None and is_pool_alive(pool_status):
        return 0

    tasks_root = run_dir / STATE_DIR / "tasks"
    if not tasks_root.exists():
        return 0

    reset_count = 0
    for status_path in tasks_root.rglob(STATUS_FILE):
        state_dir = status_path.parent
        try:
            record = read_status_at(state_dir)
        except PydanticValidationError:
            log.debug("Skipping malformed status.yaml during stale reconciliation: %s", status_path)
            continue
        if record is None or record.state != "running":
            continue

        failed_record = StatusRecord(
            run_id=record.run_id,
            step_id=record.step_id,
            item=record.item,
            state="failed",
            attempt=record.attempt,
            started_at=record.started_at,
            completed_at=_now_iso(),
            error="orphaned: pool process died while item was running",
        )
        write_status_at(state_dir, failed_record)
        reset_count += 1
        log.info(
            "Reconciled stale running item: %s (run_id=%s, attempt=%d)",
            state_dir,
            record.run_id,
            record.attempt,
        )

    if reset_count:
        log.info("Reconciled %d stale running item(s) in %s", reset_count, run_dir)
    return reset_count
