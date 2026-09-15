"""Discover RunPool event and health streams across a run and its composite scopes.

Both the ``metaproc pool`` commands and the operations summary read these streams,
so discovery lives here rather than in either consumer.
"""

from __future__ import annotations

from pathlib import Path

from metaproc import paths as paths_mod
from metaproc.io import artifact_exists, iter_artifact_paths, resolve_existing_artifact


def runpool_event_files(run_dir: Path) -> list[Path]:
    """Return event streams across ``run_dir`` and every nested composite scope.

    See :func:`metaproc.paths.iter_composite_run_dirs` for the discovery rules.
    """
    seen: set[Path] = set()
    files: list[Path] = []
    for sub_run in paths_mod.iter_composite_run_dirs(run_dir):
        for candidate in _event_files_for_one_scope(sub_run):
            if candidate in seen:
                continue
            seen.add(candidate)
            files.append(candidate)
    return files


def runpool_health_files(run_dir: Path) -> list[Path]:
    """Return health streams across ``run_dir`` and every nested composite scope."""
    seen: set[Path] = set()
    files: list[Path] = []
    for sub_run in paths_mod.iter_composite_run_dirs(run_dir):
        for candidate in _health_files_for_one_scope(sub_run):
            if candidate in seen:
                continue
            seen.add(candidate)
            files.append(candidate)
    return files


def _resolve_event_path(*, is_v2: bool, v2_path: Path, legacy_path: Path) -> Path:
    """Resolve one event stream using the V2 marker and exact legacy fallback."""
    if is_v2 or artifact_exists(v2_path):
        return resolve_existing_artifact(v2_path)
    return resolve_existing_artifact(legacy_path)


def _event_files_for_one_scope(run_dir: Path) -> list[Path]:
    """Return readable RunPool event streams for a single scope directory.

    The run-level stream plus V2 step and worker streams. Unmarked runs keep exact
    legacy fallback per stream: a present V2 stream wins for that scope, otherwise
    the matching legacy stream is used.
    """
    is_v2 = paths_mod.is_v2_run_layout(run_dir)
    candidates: list[Path] = [
        _resolve_event_path(
            is_v2=is_v2,
            v2_path=paths_mod.runpool_events(run_dir),
            legacy_path=paths_mod.legacy_runpool_events(run_dir),
        )
    ]

    step_ids: set[str] = set()
    steps_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
    if steps_root.is_dir():
        step_ids.update(
            path.parent.name
            for path in iter_artifact_paths(steps_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}")
        )
    legacy_steps_root = paths_mod.run_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
    if not is_v2 and legacy_steps_root.is_dir():
        step_ids.update(
            path.parent.name
            for path in iter_artifact_paths(legacy_steps_root, f"*/{paths_mod.POOL_EVENTS_FILE}")
        )
    for step_id in sorted(step_ids):
        candidates.append(
            _resolve_event_path(
                is_v2=is_v2,
                v2_path=paths_mod.runpool_step_events(run_dir, step_id),
                legacy_path=paths_mod.legacy_runpool_step_events(run_dir, step_id),
            )
        )

    worker_ids: set[str] = set()
    workers_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.WORKERS_SUBDIR
    if workers_root.is_dir():
        worker_ids.update(
            path.parent.name
            for path in iter_artifact_paths(workers_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}")
        )
    if not is_v2:
        worker_ids.update(
            path.parent.name
            for path in paths_mod.run_logs_dir(run_dir).glob(
                f"worker-*/{paths_mod.POOL_EVENTS_FILE}"
            )
        )
    for worker_id in sorted(worker_ids):
        candidates.append(
            _resolve_event_path(
                is_v2=is_v2,
                v2_path=paths_mod.runpool_worker_events(run_dir, worker_id),
                legacy_path=paths_mod.legacy_runpool_worker_events(run_dir, worker_id),
            )
        )

    return _existing_unique(candidates)


def _health_files_for_one_scope(run_dir: Path) -> list[Path]:
    """Return readable RunPool health streams for a single scope directory."""
    candidates: list[Path] = [resolve_existing_artifact(paths_mod.runpool_health(run_dir))]

    steps_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
    if steps_root.is_dir():
        candidates.extend(iter_artifact_paths(steps_root, f"*/{paths_mod.RUNPOOL_HEALTH_FILE}"))

    workers_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.WORKERS_SUBDIR
    if workers_root.is_dir():
        candidates.extend(iter_artifact_paths(workers_root, f"*/{paths_mod.RUNPOOL_HEALTH_FILE}"))

    return _existing_unique(candidates)


def _existing_unique(candidates: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    files: list[Path] = []
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        files.append(candidate)
    return files
