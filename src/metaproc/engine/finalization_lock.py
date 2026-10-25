"""One publisher at a time for a run's terminal summaries.

A run's orchestrator and ``metaproc status``, two processes, both publish
``resources.json``, ``resource-usage-summary.md`` and ``operations-summary.md``. Each
reads its evidence first, without the lock, then publishes under it. A writer that must
not replace what another wrote meanwhile, as status must not replace a summary the live
orchestrator wrote while status was reading, checks the files under the lock, where every
publication that finished before its own is visible and none can start until it is done.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from metaproc.io.mkdir_lock import mkdir_lock
from metaproc.paths import FINALIZATION_LOCK_DIR, STATE_DIR

FINALIZATION_LOCK_STALE_S = 5.0
"""Age after which a held lock is taken as its dead holder's and reclaimed.

The lock covers file writes only, so a live holder releases it well inside this.
"""

FINALIZATION_LOCK_TIMEOUT_S = 15.0
"""How long a publisher waits for the lock before failing; longer than the stale age."""


def finalization_lock_path(run_dir: Path) -> Path:
    """Return ``<run_dir>/.state/finalization.lock``."""
    return run_dir / STATE_DIR / FINALIZATION_LOCK_DIR


@contextmanager
def finalization_publish_lock(run_dir: Path) -> Generator[None]:
    """Hold the run's finalization lock while publishing its summaries.

    Raises :class:`~metaproc.io.mkdir_lock.MkdirLockTimeoutError` when the lock cannot be
    had in time; finalization callers already treat a failed publication as a missing
    summary, which status recovery writes later.
    """
    with mkdir_lock(
        finalization_lock_path(run_dir),
        timeout=FINALIZATION_LOCK_TIMEOUT_S,
        stale_after=FINALIZATION_LOCK_STALE_S,
    ):
        yield
