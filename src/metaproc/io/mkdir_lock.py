"""NFS-safe advisory file lock using mkdir(2).

This module is a self-contained, reusable utility. It has no dependencies on
anything outside the Python standard library, and nothing in it is specific
to metaproc. Drop it into any codebase that needs mutual exclusion across
processes sharing a filesystem.

# Why mkdir?

``mkdir(2)`` is atomic on every common filesystem, including NFSv3 and NFSv4:
the kernel guarantees either the directory is created and returns success, or
it already exists and returns ``EEXIST``. No races, no partial states.

Contrast with the alternatives:

- ``flock(2)``, ``fcntl.flock``, and ``fcntl.lockf``: byte-range or whole-file locks advisory
  to the kernel. On NFSv3 these degrade to a local-only lock (the server does
  not participate), so two workers on different VMs can both hold the "lock"
  at the same time. NFSv4 added a real lock protocol, but client support is
  inconsistent. **Do not use fcntl for cross-host mutual exclusion on NFS.**
- ``filelock`` and ``portalocker``: convenient wrappers around file-locking
  mechanisms, but they do not change the stale-state and shared-filesystem
  issues that make kernel-held locks a poor fit for Metaproc coordination.
- ``O_CREAT | O_EXCL`` on a file: also atomic, but then you are maintaining
  a regular file (content, permissions, cleanup of a non-empty file) just to
  hold a flag. mkdir is simpler: the directory itself is the flag.
- Symlink tricks: atomic, but many tools (tar, rsync) handle symlinks
  surprisingly, and ``readlink`` races with ``unlink``.

Git itself uses this exact pattern for ref updates (``.git/refs/…/HEAD.lock``
is created via ``O_CREAT | O_EXCL``, and Git's transaction dirs use mkdir).
If it's good enough for Git's ref storage on a shared NFS home directory,
it's good enough here.

# Semantics

- **Advisory**: processes must cooperate by using this lock around the
  critical section. Nothing stops a process that ignores the lock from
  touching the protected resource.
- **Exclusive**: at most one holder at a time.
- **Poll-based**: this is not kernel-assisted — contenders poll at
  ``poll_interval`` until the holder releases.
- **Stale-lock reclaim**: if the holder crashes and leaves the lock dir
  behind, contenders may treat the lock as stale once its mtime is older than
  ``stale_after`` and attempt to rmdir it. Pass ``stale_after=None`` for
  long-lived locks that must never be reclaimed by age alone, or ``is_stale``
  for a predicate that can check the recorded owner's liveness.
- **Owner metadata**: a holder may write small files directly inside the lock
  directory — its PID and host, for an ``is_stale`` predicate to read. Both
  release and stale reclaim remove those files before removing the directory,
  so owner metadata never wedges the lock. Subdirectories are not removed.
- **Fail-loud on timeout**: if the lock cannot be acquired within
  ``timeout``, :class:`MkdirLockTimeoutError` is raised. The caller must
  decide how to respond — this module never silently proceeds without the
  lock.

# Tuning

- ``timeout`` should exceed the longest critical section by a healthy
  margin.
- ``stale_after`` should exceed ``timeout`` — otherwise a legitimate
  long-running holder may have its lock reclaimed while it is still active.
  A safe ratio is ``stale_after >= 2 * timeout``.
- For locks held across child-process lifetimes, prefer ``stale_after=None``
  or a caller-specific stale predicate that can verify owner liveness.
- ``poll_interval`` trades CPU/syscall overhead against wakeup latency. 50ms
  is a reasonable default for lock holds on the order of seconds.

# Usage

    from pathlib import Path
    from metaproc.io.mkdir_lock import mkdir_lock

    with mkdir_lock(Path("/shared/critical/.lock")):
        # exclusive section — only one process at a time
        ...

The caller owns the lock path. Choose a stable location on the shared
filesystem (NFS, Filestore, SMB, local disk). The parent directory is
created if it does not exist.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_POLL_INTERVAL_S = 0.05
DEFAULT_STALE_AFTER_S = 60.0


class MkdirLockTimeoutError(RuntimeError):
    """Raised when a mkdir lock cannot be acquired within the timeout."""


@dataclass(frozen=True)
class MkdirLockLease:
    """Explicit lease for split acquire/release mkdir-lock flows."""

    lock_path: Path

    def release(self) -> None:
        """Release this lease if it is still present."""
        release_mkdir_lock(self.lock_path)


def acquire_mkdir_lock(
    lock_path: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    poll_interval: float = DEFAULT_POLL_INTERVAL_S,
    stale_after: float | None = DEFAULT_STALE_AFTER_S,
    is_stale: Callable[[Path], bool] | None = None,
) -> MkdirLockLease:
    """Acquire a mkdir lock and return an explicit release handle.

    Prefer :func:`mkdir_lock` when the critical section can be expressed as a
    context manager. Use this function only when acquisition and release are
    necessarily split across separate lifecycle calls.

    Args:
        lock_path: Directory path used as the lock token.
        timeout: Maximum seconds to wait for acquisition before raising.
        poll_interval: Seconds between attempts when the lock is held.
        stale_after: Age-based stale-reclaim threshold. ``None`` disables
            age-based reclaim.
        is_stale: Optional caller-specific stale predicate. When provided, it
            decides whether a held lock should be reclaimed.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + timeout
    while True:
        try:
            lock_path.mkdir()
            return MkdirLockLease(lock_path=lock_path)
        except FileExistsError:
            if _lock_should_be_reclaimed(
                lock_path,
                stale_after=stale_after,
                is_stale=is_stale,
            ) and _reclaim_lock_dir(lock_path):
                # Reclaimed: retry at once rather than waiting out a poll interval.
                continue
            # A lock that should be reclaimed but cannot be — one whose path is not a
            # lock at all, or whose directory this process may not write — still has to
            # reach the deadline. Retrying a failing reclaim without it would spin.
            if time.monotonic() >= deadline:
                msg = (
                    f"could not acquire lock {lock_path} within {timeout:.0f}s "
                    f"(held by another process; delete the lock directory if "
                    f"no process is active)"
                )
                raise MkdirLockTimeoutError(msg) from None
            time.sleep(poll_interval)


def _lock_should_be_reclaimed(
    lock_path: Path,
    *,
    stale_after: float | None,
    is_stale: Callable[[Path], bool] | None,
) -> bool:
    try:
        if is_stale is not None:
            return is_stale(lock_path)
        if stale_after is None:
            return False
        age = time.time() - lock_path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age > stale_after


def _reclaim_lock_dir(lock_path: Path) -> bool:
    """Try to remove a lock judged stale; return whether the path is now free.

    A reclaim that cannot be done is not an error here — the contender falls back to
    waiting and, in time, to a loud timeout.
    """
    try:
        _remove_lock_dir(lock_path)
    except FileNotFoundError:
        return True  # Another contender got there first.
    except OSError:
        return False
    return True


def _remove_lock_dir(lock_path: Path) -> None:
    """Remove a lock directory, discarding owner metadata a holder left inside it.

    Only regular files directly inside the directory are removed, and only ones
    that are not themselves links: a lock directory holds a holder's own small
    records and nothing else.

    A lock path that is a symlink is refused rather than followed. A lock is a
    directory this module created with ``mkdir(2)``, so a symlink in its place is
    not a lock, and deleting through one would reach files outside the lock --
    which matters most on exactly the shared filesystems this module is for,
    where the lock's parent may be writable by another tenant. Refusing makes
    such a lock unacquirable, which is loud, rather than destructive.
    """
    if lock_path.is_symlink():
        msg = f"refusing to remove a lock path that is a symlink: {lock_path}"
        raise NotADirectoryError(msg)
    try:
        entries = list(lock_path.iterdir())
    except FileNotFoundError, NotADirectoryError:
        entries = []
    for entry in entries:
        if entry.is_file(follow_symlinks=False):
            with contextlib.suppress(FileNotFoundError, OSError):
                entry.unlink()
    lock_path.rmdir()


def release_mkdir_lock(lock_path: Path) -> bool:
    """Release a mkdir lock path if it exists.

    Owner metadata the holder wrote directly inside the lock directory is
    removed with it. Returns ``True`` when the directory was removed and
    ``False`` when it was already absent. Other ``OSError`` cases are left to
    the caller so split acquire/release flows can log stuck locks.
    """
    try:
        _remove_lock_dir(lock_path)
    except FileNotFoundError:
        return False
    return True


@contextlib.contextmanager
def mkdir_lock(
    lock_path: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    poll_interval: float = DEFAULT_POLL_INTERVAL_S,
    stale_after: float | None = DEFAULT_STALE_AFTER_S,
) -> Generator[None]:
    """Hold an exclusive advisory lock at ``lock_path``.

    The lock is represented by a directory at ``lock_path``. Acquisition
    calls ``mkdir`` until it succeeds, treating ``EEXIST`` as "held by
    someone else" and polling until the holder releases (or the lock is
    reclaimed as stale).

    Args:
        lock_path: Where to create the lock directory. Parent is created
            if missing.
        timeout: Maximum seconds to wait for acquisition before raising
            :class:`MkdirLockTimeoutError`.
        poll_interval: Seconds between attempts when the lock is held.
        stale_after: Seconds after which a held lock is considered stale
            (crashed holder) and may be reclaimed. ``None`` disables
            age-based reclaim.

    Raises:
        MkdirLockTimeoutError: Lock could not be acquired within ``timeout``.
    """
    lease = acquire_mkdir_lock(
        lock_path,
        timeout=timeout,
        poll_interval=poll_interval,
        stale_after=stale_after,
    )
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lease.release()
