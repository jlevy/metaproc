"""The owned-launch primitive: spawn without forking the supervisor, observe exit without
``SIGCHLD``, and know when the target is actually executing.

CPython's ``subprocess`` cannot provide this in the configuration a supervisor needs.
With ``start_new_session=True`` or the default ``close_fds=True`` it falls back to
``fork_exec``, which on macOS is a real ``fork`` of the parent, and ``fork`` is the
operation that waits under memory pressure. ``asyncio`` child watchers assume ``Popen``.

This module uses four standard-library pieces instead:

1. ``os.posix_spawn`` with ``setsid=True`` creates the isolated session and process
   group without forking the supervisor.
2. A pipe with its write end mapped into the child carries the handshake: the wrapper
   writes one byte when it has registered, and the descriptor closes on ``exec``.
   EOF without the byte means the wrapper died before ``exec``.
3. Exit is observed on Linux through ``pidfd_open``, which cannot be recycled and is
   readable on exit, and on macOS through ``kqueue`` ``EVFILT_PROC``; both integrate
   with the event loop's reader. A waiter thread is the fallback.
4. The child is reaped with ``waitpid`` on the observed PID.

The wrapper is ``safeproc._launch_wrapper``; it changes directory when asked, performs
the handshake, and replaces itself with the target. Descriptor hygiene comes from PEP
446: Python creates descriptors non-inheritable, so only the ones passed through
``file_actions`` reach the child.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import selectors
import signal
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

HANDSHAKE_FD = 3
"""The descriptor number the wrapper finds the handshake pipe on."""

HANDSHAKE_READY = b"R"


class ExitKind(StrEnum):
    EXITED = "exited"
    SIGNALED = "signaled"


@dataclass(frozen=True)
class ExitStatus:
    kind: ExitKind
    code: int
    """Exit code when ``EXITED``, signal number when ``SIGNALED``."""

    @property
    def ok(self) -> bool:
        return self.kind is ExitKind.EXITED and self.code == 0


def _decode_status(status: int) -> ExitStatus:
    if os.WIFSIGNALED(status):
        return ExitStatus(ExitKind.SIGNALED, os.WTERMSIG(status))
    return ExitStatus(ExitKind.EXITED, os.WEXITSTATUS(status))


class Handshake(StrEnum):
    """What the pipe said about the wrapper."""

    EXECUTING = "executing"
    """The wrapper registered and replaced itself with the target."""

    WRAPPER_FAILED = "wrapper_failed"
    """The pipe closed before the ready byte: the wrapper died before ``exec``."""

    NONE = "none"
    """No wrapper was used; the target was spawned directly."""


@dataclass
class Launched:
    """One spawned process, with the handles needed to observe it."""

    pid: int
    pidfd: int | None
    handshake_fd: int | None
    """Read end of the handshake pipe, or ``None`` when no wrapper was used."""

    def close(self) -> None:
        for fd in (self.pidfd, self.handshake_fd):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
        self.pidfd = None
        self.handshake_fd = None

    def send_signal(self, sig: int) -> bool:
        """Signal the identity, not the PID, where the platform can."""
        try:
            if self.pidfd is not None and hasattr(signal, "pidfd_send_signal"):
                signal.pidfd_send_signal(self.pidfd, sig)
            else:
                os.kill(self.pid, sig)
        except ProcessLookupError:
            return False
        except PermissionError:
            return False
        return True


def _open_pidfd(pid: int) -> int | None:
    opener = getattr(os, "pidfd_open", None)
    if opener is None:
        return None
    try:
        return int(opener(pid))
    except OSError:
        return None


def spawn_isolated(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    wrapper: bool = True,
    stdin: int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
) -> Launched:
    """Spawn ``argv`` in a new session and process group without forking the caller.

    With ``wrapper`` the child is the launch wrapper, which handshakes and then ``exec``s
    the target; that is the only way to support ``cwd`` without a fork, and the only way
    to know when the target is executing. Without it the target is spawned directly and
    ``handshake_fd`` is ``None``.
    """
    if not argv:
        raise ValueError("argv must not be empty")
    file_actions: list[tuple[int, ...]] = []
    for fd, target in ((stdin, 0), (stdout, 1), (stderr, 2)):
        if fd is not None:
            file_actions.append((os.POSIX_SPAWN_DUP2, fd, target))

    read_end: int | None = None
    write_end: int | None = None
    if wrapper:
        read_end, write_end = os.pipe()
        file_actions.append((os.POSIX_SPAWN_DUP2, write_end, HANDSHAKE_FD))
        command = [
            sys.executable,
            "-m",
            "safeproc._launch_wrapper",
            cwd or "",
            "--",
            *argv,
        ]
    elif cwd is not None:
        raise ValueError("cwd requires the wrapper; posix_spawn cannot change directory")
    else:
        command = list(argv)

    environment = dict(os.environ if env is None else env)
    try:
        pid = os.posix_spawnp(
            command[0],
            command,
            environment,
            file_actions=file_actions,
            setsid=True,
        )
    finally:
        if write_end is not None:
            os.close(write_end)
    return Launched(pid=pid, pidfd=_open_pidfd(pid), handshake_fd=read_end)


def read_handshake(launched: Launched, timeout: float | None = None) -> Handshake:
    """Block until the wrapper reports, the pipe closes, or ``timeout`` elapses.

    Raises ``TimeoutError`` on timeout so a hung wrapper is distinguishable from one that
    died.
    """
    fd = launched.handshake_fd
    if fd is None:
        return Handshake.NONE
    selector = selectors.DefaultSelector()
    selector.register(fd, selectors.EVENT_READ)
    seen = b""
    try:
        while True:
            if not selector.select(timeout):
                raise TimeoutError("launch wrapper did not report")
            chunk = os.read(fd, 16)
            if not chunk:
                break
            seen += chunk
            if HANDSHAKE_READY in seen:
                break
    finally:
        selector.close()
    if HANDSHAKE_READY in seen:
        # Drain to EOF so the descriptor can be closed without a race with exec.
        return Handshake.EXECUTING
    return Handshake.WRAPPER_FAILED


async def read_handshake_async(launched: Launched) -> Handshake:
    """Await the handshake on the loop. Bound it with ``asyncio.timeout`` at the call site."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, read_handshake, launched, None)


def wait_exit(launched: Launched, timeout: float | None = None) -> ExitStatus:
    """Block until the process exits and reap it. ``TimeoutError`` if it does not."""
    if launched.pidfd is not None:
        selector = selectors.DefaultSelector()
        selector.register(launched.pidfd, selectors.EVENT_READ)
        try:
            if not selector.select(timeout):
                raise TimeoutError("process did not exit")
        finally:
            selector.close()
        _, status = os.waitpid(launched.pid, 0)
        return _decode_status(status)
    if sys.platform == "darwin":
        return _wait_exit_kqueue(launched.pid, timeout)
    return _wait_exit_thread(launched.pid, timeout)


def _wait_exit_kqueue(pid: int, timeout: float | None) -> ExitStatus:
    import select  # noqa: PLC0415 -- kqueue exists only on BSD-derived platforms

    kq = select.kqueue()
    try:
        event = select.kevent(
            pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_ONESHOT, select.KQ_NOTE_EXIT
        )
        try:
            kq.control([event], 0)
        except ProcessLookupError:
            pass  # already exited; waitpid below reaps it
        else:
            if not kq.control(None, 1, timeout):
                raise TimeoutError("process did not exit")
    finally:
        kq.close()
    _, status = os.waitpid(pid, 0)
    return _decode_status(status)


def _wait_exit_thread(pid: int, timeout: float | None) -> ExitStatus:
    result: list[int] = []
    done = threading.Event()

    def reap() -> None:
        _, status = os.waitpid(pid, 0)
        result.append(status)
        done.set()

    threading.Thread(target=reap, name=f"safeproc-wait-{pid}", daemon=True).start()
    if not done.wait(timeout):
        raise TimeoutError("process did not exit")
    return _decode_status(result[0])


async def wait_exit_async(launched: Launched) -> ExitStatus:
    """Await exit on the event loop: a reader on the pidfd or kqueue, never ``SIGCHLD``.

    Bound it with ``asyncio.timeout`` at the call site.
    """
    loop = asyncio.get_running_loop()
    if launched.pidfd is not None:
        exited = loop.create_future()
        loop.add_reader(launched.pidfd, exited.set_result, None)
        try:
            await exited
        finally:
            loop.remove_reader(launched.pidfd)
        # The pidfd is readable only once the process has exited, so this reap returns
        # immediately; it does not block the loop.
        _, status = os.waitpid(launched.pid, 0)  # noqa: ASYNC222 -- already exited
        return _decode_status(status)
    return await loop.run_in_executor(None, wait_exit, launched, None)
