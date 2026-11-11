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
import errno
import logging
import os
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

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
    waiting and, in time, to a loud timeout. It is logged, because an acquisition
    that times out against a lock nobody holds is otherwise unexplainable.
    """
    try:
        _remove_lock_dir(lock_path)
    except FileNotFoundError:
        return True  # Another contender got there first.
    except OSError:
        log.debug("could not reclaim the stale lock %s", lock_path, exc_info=True)
        return False
    return True


_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_PINNED_REMOVAL = bool(
    _O_DIRECTORY
    and _O_NOFOLLOW
    and os.unlink in os.supports_dir_fd
    and os.scandir in os.supports_fd
)
"""Whether this platform can remove a lock through a handle on the directory itself."""


def _remove_lock_dir(lock_path: Path) -> None:
    """Remove a lock directory, discarding owner metadata a holder left inside it.

    Only regular files directly inside the directory are removed, and only ones
    that are not themselves links: a lock directory holds a holder's own small
    records and nothing else.

    Everything here is pinned to one directory, the instance open at ``lock_path``
    when removal starts, because two things can replace that path while this runs
    and both are load-bearing:

    - Another process that can write the lock's **parent** can swap the directory
      for a symlink. Following it would delete files outside the lock, on exactly
      the shared filesystems this module is for. ``O_NOFOLLOW`` refuses a symlink
      in one step rather than checking and then re-resolving, which is a race.
    - Another contender can reclaim this same lock and a new holder can acquire
      the path, all between listing the directory and emptying it. Deleting that
      holder's owner record and its lock would put two holders in the critical
      section. Children are unlinked relative to the open directory, so they
      cannot be redirected, and the instance is confirmed unchanged before the
      final ``rmdir``.

    Raises ``NotADirectoryError`` for a symlinked path and ``FileExistsError``
    when the lock was replaced part way, so a reclaimer treats either as "not
    reclaimed" and waits for the new holder instead.
    """
    if not _PINNED_REMOVAL:  # pragma: no cover - Windows and other platforms
        _remove_lock_dir_by_path(lock_path)
        return
    try:
        fd = os.open(lock_path, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except NotADirectoryError:
        raise
    except OSError as exc:
        # ELOOP is how O_NOFOLLOW reports a symlink; name it for the caller.
        if exc.errno == errno.ELOOP:
            msg = f"refusing to remove a lock path that is a symlink: {lock_path}"
            raise NotADirectoryError(msg) from exc
        raise
    try:
        for entry in os.scandir(fd):
            if entry.is_file(follow_symlinks=False):
                with contextlib.suppress(OSError):
                    os.unlink(entry.name, dir_fd=fd)
        pinned = os.fstat(fd)
        current = os.lstat(lock_path)
        if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
            msg = f"lock {lock_path} was replaced while it was being reclaimed"
            raise FileExistsError(msg)
    finally:
        os.close(fd)
    # `rmdir` never follows a final symlink, so a swap after the check above fails
    # here rather than removing a replacement.
    os.rmdir(lock_path)


def _remove_lock_dir_by_path(lock_path: Path) -> None:  # pragma: no cover - see above
    """Remove a lock directory on a platform without directory handles.

    The symlink refusal is a check followed by a use, so it closes the ordinary
    mistake rather than a race with a process that can write the lock's parent.
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
            with contextlib.suppress(OSError):
                entry.unlink()
    lock_path.rmdir()


def release_mkdir_lock(lock_path: Path) -> bool:
    """Release a mkdir lock path if it exists.

    Owner metadata the holder wrote directly inside the lock directory is
    removed with it. Returns ``True`` when the directory was removed and
    ``False`` when it was already absent. Other ``OSError`` cases are left to
    the caller so split acquire/release flows can log stuck locks; that includes
    a lock replaced at its path while this ran, which is not ours to remove.
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
        try:
            lease.release()
        except OSError:
            log.debug("could not release the lock %s", lock_path, exc_info=True)
