"""One publisher at a time for a run's terminal summaries.

A run's orchestrator and ``metaproc status``, two processes, both publish
``resources.json``, ``resource-usage-summary.md`` and ``operations-summary.md``. Each
reads its evidence first, without the lock, then publishes under it. A writer that must
not replace what another wrote meanwhile, as status must not replace a summary the live
orchestrator wrote while status was reading, checks the files under the lock, where every
publication that finished before its own is visible and none can start until it is done.

For that check to mean anything, the lock has to exclude. So a held lock is reclaimed
only when its holder is known to be gone: the holder records its PID and host inside the
lock directory, and a contender on the same host reclaims the lock when that PID is no
longer running. Age alone reclaims only after :data:`FINALIZATION_LOCK_STALE_S`, long
past any publication, and only that covers a holder on another host or a lock with no
readable owner. Release removes the lock only while this process still owns it, so a
publisher that was reclaimed as dead cannot take a live contender's lock away.

One holder is special: a hard-stop signal interrupts the main thread wherever it is,
including inside a publication, and the signal-exit work must still publish from its own
thread within its bound. It cannot wait for that lock, which the interrupted thread will
never release, and it must not reclaim a lock a live process holds. So it inherits the
lease instead, with ``adopt_local_holder=True``: the publication it interrupted is
abandoned with the process, every publication writes atomically, and the signal-exit
work is the last writer in this process either way.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psutil

from metaproc.io.mkdir_lock import acquire_mkdir_lock
from metaproc.paths import FINALIZATION_LOCK_DIR, STATE_DIR

log = logging.getLogger(__name__)

FINALIZATION_LOCK_OWNER_FILE = "owner"
"""Holder record inside the lock directory: ``<pid> <host>`` on one line."""

FINALIZATION_LOCK_STALE_S = 300.0
"""Age at which a held lock is reclaimed without knowing whether its holder died.

Only a holder on another host, or one whose owner record does not read, is reclaimed by
age; a holder on this host is reclaimed as soon as its PID is gone. So this is a last
resort, and it is far longer than any publication: a reclaim while the holder still
writes would let two publishers into the same critical section.
"""

FINALIZATION_LOCK_TIMEOUT_S = 15.0
"""How long a publisher waits for the lock before failing.

A caller treats a failed publication as a missing summary, which recovery writes later.
"""


@dataclass(frozen=True)
class _LocalHolder:
    """This process's hold on a finalization lock, as its own threads see it."""

    lock_path: Path
    pid: int


_local_holder: _LocalHolder | None = None
"""The lock this process holds, if any. Read by signal-exit work on its own thread."""


def finalization_lock_path(run_dir: Path) -> Path:
    """Return ``<run_dir>/.state/finalization.lock``."""
    return run_dir / STATE_DIR / FINALIZATION_LOCK_DIR


@contextmanager
def finalization_publish_lock(
    run_dir: Path,
    *,
    adopt_local_holder: bool = False,
    timeout_s: float = FINALIZATION_LOCK_TIMEOUT_S,
) -> Generator[None]:
    """Hold the run's finalization lock while publishing its summaries.

    ``adopt_local_holder=True`` publishes under a lock this same process already holds,
    rather than waiting for it, and is only for the work a hard-stop signal runs: the
    thread that holds the lock has been interrupted and never resumes. ``timeout_s``
    shortens the wait for a caller that would rather fail than queue.

    Raises :class:`~metaproc.io.mkdir_lock.MkdirLockTimeoutError` when the lock cannot be
    had in time; finalization callers already treat a failed publication as a missing
    summary, which status recovery writes later.
    """
    global _local_holder  # noqa: PLW0603 - one process-wide record of this process's hold

    lock_path = finalization_lock_path(run_dir)
    held = _local_holder
    if adopt_local_holder and held is not None and held.lock_path == lock_path:
        if held.pid == os.getpid():
            # The interrupted publisher never resumes; its lease passes here.
            yield
            return
        # Inherited across a fork: the record is the parent's, not a hold of ours.
        _local_holder = None

    lease = acquire_mkdir_lock(
        lock_path,
        timeout=timeout_s,
        stale_after=FINALIZATION_LOCK_STALE_S,
        is_stale=_holder_is_gone,
    )
    _write_owner(lock_path)
    previous = _local_holder
    _local_holder = _LocalHolder(lock_path=lock_path, pid=os.getpid())
    try:
        yield
    finally:
        _local_holder = previous
        if _owner_is_self(lock_path):
            lease.release()
        else:
            # Reclaimed as dead while we published; the lock is a live contender's now.
            log.warning("finalization lock %s was reclaimed while held; not releasing", lock_path)


def _write_owner(lock_path: Path) -> None:
    """Record this process as the lock's holder, for a contender's liveness check."""
    try:
        # write-contract: create-only -- the holder writes this once, inside the
        # directory whose atomic `mkdir` is what made it the holder; "x" refuses an
        # existing record, which would be another publisher's.
        with (lock_path / FINALIZATION_LOCK_OWNER_FILE).open("x", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()} {socket.gethostname()}\n")
    except OSError:
        # Without the record the lock still excludes; it is then reclaimed by age only.
        log.debug("could not record the finalization lock holder at %s", lock_path, exc_info=True)


def _read_owner(lock_path: Path) -> tuple[int, str] | None:
    """Return the lock holder's ``(pid, host)``, or ``None`` if none reads."""
    try:
        raw = (lock_path / FINALIZATION_LOCK_OWNER_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    pid_text, _, host = raw.strip().partition(" ")
    try:
        return int(pid_text), host.strip()
    except ValueError:
        return None


def _owner_is_self(lock_path: Path) -> bool:
    """Return whether the lock still records this process as its holder."""
    owner = _read_owner(lock_path)
    if owner is None:
        # No record reads. Only a holder writes one, so the lock is still this hold's.
        return True
    return owner == (os.getpid(), socket.gethostname())


def _holder_is_gone(lock_path: Path) -> bool:
    """Return whether a held lock may be reclaimed, as its holder is gone.

    A holder this host can probe is reclaimed the moment its PID is gone, and never
    before: age is no evidence that a running publisher has stopped writing, and evicting
    one would put two publishers in the same critical section. Only a holder on another
    host, or one whose record does not read, falls back to age, after
    :data:`FINALIZATION_LOCK_STALE_S` — far longer than any publication takes.
    """
    owner = _read_owner(lock_path)
    if owner is not None:
        pid, host = owner
        if host == socket.gethostname() and pid != os.getpid():
            return not _pid_is_running(pid)
    return _lock_age_s(lock_path) > FINALIZATION_LOCK_STALE_S


def _pid_is_running(pid: int) -> bool:
    """Return whether *pid* names a live process on this host."""
    if pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001 - an unanswerable probe keeps the holder alive
        return True


def _lock_age_s(lock_path: Path) -> float:
    try:
        return time.time() - lock_path.stat().st_mtime
    except OSError:
        return 0.0
