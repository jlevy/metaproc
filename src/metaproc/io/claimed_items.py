"""Claimed-item registry helpers for cloud worker scaling."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from frontmatter_format import read_yaml_file, to_yaml_string
from pydantic import BaseModel, Field
from strif import atomic_output_file

from metaproc.io.mkdir_lock import MkdirLockTimeoutError, mkdir_lock
from metaproc.paths import CLAIMED_ITEMS_FILE, step_state_dir

CLAIM_LOCK_DIR = ".claim.lock"
CLAIM_LOCK_TIMEOUT_S = 30.0
CLAIM_LOCK_POLL_S = 0.05
CLAIM_LOCK_STALE_S = 60.0


ClaimLockTimeoutError = MkdirLockTimeoutError


class ClaimedItemsRecord(BaseModel):
    """Items currently owned by a worker for a fan-out step."""

    worker_id: str
    updated_at: str
    items: list[str] = Field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")


def claimed_items_path(run_dir: Path, step_id: str, worker_id: str) -> Path:
    """Return the per-worker claim registry path.

    Layout: ``<run>/.state/steps/<step_id>/worker-<id>/claimed-items.yaml``.
    """
    return step_state_dir(run_dir, step_id) / f"worker-{worker_id}" / CLAIMED_ITEMS_FILE


def read_claimed_items(run_dir: Path, step_id: str, worker_id: str) -> ClaimedItemsRecord | None:
    """Read one worker's claim registry, if present."""
    path = claimed_items_path(run_dir, step_id, worker_id)
    if not path.exists():
        return None
    raw = read_yaml_file(path)
    return ClaimedItemsRecord.model_validate(raw)


def write_claimed_items(
    run_dir: Path,
    step_id: str,
    *,
    worker_id: str,
    items: list[str],
) -> Path:
    """Atomically write one worker's claim registry."""
    path = claimed_items_path(run_dir, step_id, worker_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = ClaimedItemsRecord(worker_id=worker_id, updated_at=_now_iso(), items=items)
    with atomic_output_file(path) as tmp_path:
        Path(tmp_path).write_text(
            to_yaml_string(record.model_dump()),
            encoding="utf-8",
        )
    return path


def collect_claimed_items(
    run_dir: Path,
    step_id: str,
    *,
    exclude_worker_id: str | None = None,
    worker_ids: set[str] | None = None,
) -> set[str]:
    """Collect claimed items across all workers for a step."""
    root = step_state_dir(run_dir, step_id)
    if not root.exists():
        return set()

    claimed: set[str] = set()
    for worker_dir in sorted(root.glob("worker-*")):
        if not worker_dir.is_dir():
            continue
        worker_id = worker_dir.name.removeprefix("worker-")
        if worker_ids is not None and worker_id not in worker_ids:
            continue
        if exclude_worker_id is not None and worker_id == exclude_worker_id:
            continue
        record = read_claimed_items(run_dir, step_id, worker_id)
        if record is None:
            continue
        claimed.update(record.items)
    return claimed


@contextmanager
def _step_claim_lock(run_dir: Path, step_id: str) -> Generator[None]:
    """Serialize claim_item for a step via an NFS-safe mkdir lock.

    See :mod:`metaproc.io.mkdir_lock` for the shared-filesystem locking
    primitive and rationale.
    """
    lock_path = step_state_dir(run_dir, step_id) / CLAIM_LOCK_DIR
    with mkdir_lock(
        lock_path,
        timeout=CLAIM_LOCK_TIMEOUT_S,
        poll_interval=CLAIM_LOCK_POLL_S,
        stale_after=CLAIM_LOCK_STALE_S,
    ):
        yield


def claim_item(run_dir: Path, step_id: str, *, worker_id: str, item: str) -> bool:
    """Claim an item for one worker unless another worker already owns it."""
    with _step_claim_lock(run_dir, step_id):
        current = read_claimed_items(run_dir, step_id, worker_id)
        current_items = list(current.items) if current is not None else []
        if item in current_items:
            return True

        if item in collect_claimed_items(run_dir, step_id, exclude_worker_id=worker_id):
            return False

        current_items.append(item)
        write_claimed_items(run_dir, step_id, worker_id=worker_id, items=current_items)
        return True


def clear_claimed_items(run_dir: Path, step_id: str, *, worker_id: str) -> None:
    """Delete one worker's claim registry if it exists."""
    path = claimed_items_path(run_dir, step_id, worker_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
