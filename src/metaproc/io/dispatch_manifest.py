"""Dispatch manifest — per-step record of submitted worker Batch jobs.

Written after ``dispatch_to_workers()`` submits jobs, read on resume to
adopt running workers instead of blindly redispatching.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from frontmatter_format import read_yaml_file, to_yaml_string
from strif import atomic_output_file

from metaproc.paths import DISPATCH_MANIFEST_FILE, step_state_dir

log = logging.getLogger(__name__)


def _manifest_path(run_dir: Path, step_id: str) -> Path:
    """Return manifest path: ``<run>/.state/steps/<step_id>/dispatch-manifest.yaml``."""
    return step_state_dir(run_dir, step_id) / DISPATCH_MANIFEST_FILE


def write_dispatch_manifest(
    run_dir: Path,
    step_id: str,
    *,
    worker_jobs: Sequence[object],
    num_items: int,
    variant: str = "",
) -> Path:
    """Persist the dispatch manifest after workers are submitted.

    ``worker_jobs`` is the list returned by ``_submit_workers()`` — each
    dict has ``worker_id``, ``job_name``, ``job_id``, ``items_count``,
    and ``items``.
    """
    path = _manifest_path(run_dir, step_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, object] = {
        "step_id": step_id,
        "dispatched_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        "num_items": num_items,
        "num_workers": len(worker_jobs),
        "variant": variant,
        "workers": worker_jobs,
    }

    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(to_yaml_string(data), encoding="utf-8")

    log.info("Wrote dispatch manifest: %s (%d workers)", path, len(worker_jobs))
    return path


def read_dispatch_manifest(run_dir: Path, step_id: str) -> dict[str, object] | None:
    """Read the dispatch manifest for a step, or None if it doesn't exist."""
    path = _manifest_path(run_dir, step_id)
    if not path.exists():
        return None

    try:
        raw = read_yaml_file(path)
    except Exception:
        log.warning("Corrupt dispatch manifest: %s", path, exc_info=True)
        return None
    if not isinstance(raw, dict):
        log.warning("Corrupt dispatch manifest: %s", path)
        return None
    return raw


def append_dispatch_manifest(
    run_dir: Path,
    step_id: str,
    *,
    worker_jobs: Sequence[object],
) -> Path:
    """Append newly submitted workers to an existing dispatch manifest."""
    existing = read_dispatch_manifest(run_dir, step_id)
    if existing is None:
        return write_dispatch_manifest(run_dir, step_id, worker_jobs=worker_jobs, num_items=0)

    workers = existing.get("workers")
    merged_workers = list(workers) if isinstance(workers, list) else []
    merged_workers.extend(worker_jobs)

    data: dict[str, object] = dict(existing)
    data["workers"] = merged_workers
    data["num_workers"] = len(merged_workers)

    path = _manifest_path(run_dir, step_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(to_yaml_string(data), encoding="utf-8")
    return path
