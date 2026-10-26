"""The finalization lock excludes: it is reclaimed only from a holder that is gone.

A publisher checks the run's artifacts under this lock and then publishes, so a reclaim
while its holder still writes would let two publishers into the same critical section and
make that check worthless. Age alone is therefore not grounds to reclaim a holder this
host can probe, and release cannot take a lock another publisher now holds.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from metaproc.engine.finalization_lock import (
    FINALIZATION_LOCK_OWNER_FILE,
    FINALIZATION_LOCK_STALE_S,
    finalization_lock_path,
    finalization_publish_lock,
)
from metaproc.io.mkdir_lock import MkdirLockTimeoutError

_SHORT_WAIT_S = 0.3


def _held_by(run_dir: Path, *, pid: int, host: str | None = None, age_s: float = 0.0) -> Path:
    """Create the run's lock directory as *pid* on *host* would leave it."""
    lock_path = finalization_lock_path(run_dir)
    lock_path.mkdir(parents=True)
    (lock_path / FINALIZATION_LOCK_OWNER_FILE).write_text(
        f"{pid} {host if host is not None else platform.node()}\n", encoding="utf-8"
    )
    if age_s:
        old = time.time() - age_s
        os.utime(lock_path, (old, old))
    return lock_path


def _exited_pid() -> int:
    """The PID of a process that has exited and been reaped, so nothing owns it."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait(timeout=60)
    return proc.pid


def test_a_live_holder_is_not_reclaimed_however_old_its_lock_is(tmp_path: Path) -> None:
    """The holder is alive on this host, so its age proves nothing: a contender waits."""
    _held_by(tmp_path, pid=os.getppid(), age_s=FINALIZATION_LOCK_STALE_S * 4)

    with pytest.raises(MkdirLockTimeoutError):  # noqa: PT012 - the lock is the subject
        with finalization_publish_lock(tmp_path, timeout_s=_SHORT_WAIT_S):
            pass

    assert finalization_lock_path(tmp_path).is_dir()


def test_a_dead_holder_is_reclaimed_at_once(tmp_path: Path) -> None:
    """A crashed publisher leaves its lock behind; the next publisher does not wait."""
    _held_by(tmp_path, pid=_exited_pid())

    started = time.monotonic()
    with finalization_publish_lock(tmp_path, timeout_s=_SHORT_WAIT_S):
        assert finalization_lock_path(tmp_path).is_dir()
    elapsed = time.monotonic() - started

    assert elapsed < _SHORT_WAIT_S, f"reclaiming a dead holder's lock took {elapsed:.2f}s"
    assert not finalization_lock_path(tmp_path).exists()


def test_a_holder_on_another_host_is_reclaimed_only_by_age(tmp_path: Path) -> None:
    """This host cannot probe it, so only an age far beyond any publication reclaims it."""
    lock_path = _held_by(tmp_path, pid=1, host="some-other-host", age_s=1.0)

    with pytest.raises(MkdirLockTimeoutError):  # noqa: PT012 - the lock is the subject
        with finalization_publish_lock(tmp_path, timeout_s=_SHORT_WAIT_S):
            pass

    old = time.time() - (FINALIZATION_LOCK_STALE_S + 60)
    os.utime(lock_path, (old, old))
    with finalization_publish_lock(tmp_path, timeout_s=_SHORT_WAIT_S):
        pass

    assert not lock_path.exists()


def test_an_unowned_lock_is_reclaimed_only_by_age(tmp_path: Path) -> None:
    """A lock with no owner record — a holder that could not write one, or an older
    Metaproc — keeps the age fallback."""
    lock_path = finalization_lock_path(tmp_path)
    lock_path.mkdir(parents=True)

    with pytest.raises(MkdirLockTimeoutError):  # noqa: PT012 - the lock is the subject
        with finalization_publish_lock(tmp_path, timeout_s=_SHORT_WAIT_S):
            pass

    old = time.time() - (FINALIZATION_LOCK_STALE_S + 60)
    os.utime(lock_path, (old, old))
    with finalization_publish_lock(tmp_path, timeout_s=_SHORT_WAIT_S):
        pass

    assert not lock_path.exists()


def test_release_does_not_remove_another_publishers_lock(tmp_path: Path) -> None:
    """Our lock was reclaimed as dead and a contender holds it now: we must leave it."""
    lock_path = finalization_lock_path(tmp_path)

    with finalization_publish_lock(tmp_path):
        assert lock_path.is_dir()
        # What a contender that reclaimed and re-created the lock leaves behind.
        (lock_path / FINALIZATION_LOCK_OWNER_FILE).write_text(
            f"{os.getppid()} {platform.node()}\n", encoding="utf-8"
        )

    assert lock_path.is_dir(), "release removed a lock this process no longer owned"


def test_signal_exit_work_inherits_a_lock_this_process_holds(tmp_path: Path) -> None:
    """A hard-stop signal interrupts the main thread inside a publication: its work
    publishes from another thread at once rather than waiting out a lease that thread
    can never release."""
    waited: list[float] = []

    def signal_exit_publishes() -> None:
        started = time.monotonic()
        with finalization_publish_lock(tmp_path, adopt_local_holder=True):
            waited.append(time.monotonic() - started)

    with finalization_publish_lock(tmp_path):
        worker = threading.Thread(target=signal_exit_publishes)
        worker.start()
        worker.join(timeout=30)

    assert waited, "the signal-exit publication never ran"
    assert waited[0] < 1.0, f"it waited {waited[0]:.1f}s for a lock of its own process"


def test_an_ordinary_publisher_still_waits_for_the_lock(tmp_path: Path) -> None:
    """Only signal-exit work inherits the lease; another publisher waits its turn."""
    with pytest.raises(MkdirLockTimeoutError):  # noqa: PT012 - the hold is the fixture
        with finalization_publish_lock(tmp_path):
            with finalization_publish_lock(tmp_path, timeout_s=_SHORT_WAIT_S):
                pass

    assert not finalization_lock_path(tmp_path).exists()
