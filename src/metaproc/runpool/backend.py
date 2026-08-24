"""LaunchBackend protocol and LocalBackend implementation.

The ``LaunchBackend`` protocol abstracts process lifecycle management so the
pool is not hardcoded to local subprocesses.  ``LocalBackend`` ships with
runpool.  Additional backends can implement the same protocol for
alternative execution environments.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import psutil

log = logging.getLogger(__name__)

# How long supervised launches get to exit gracefully before escalation.
_PROCESS_TERMINATION_GRACE_S = 5.0
# How long Metaproc waits for the process group after an uncatchable kill signal.
_PROCESS_KILL_WAIT_S = 5.0
# Default cadence for scalar launch supervision.
_PROCESS_POLL_INTERVAL_S = 0.1
# Poll cadence while waiting for a signalled process group to disappear.
_PROCESS_GROUP_EXIT_POLL_INTERVAL_S = 0.05


# Substrings that mark an env-var name as carrying credential material. When
# we write the invocation sidecar we keep the key (so drift in which secrets
# are present is visible) but redact the value to a length-only marker.
# Never include the actual secret in artifacts that may end up in committed
# logs, run reports, or shared filesystem snapshots.
_SENSITIVE_ENV_NAME_MARKERS = (
    "TOKEN",
    "KEY",
    "SECRET",
    "PASSWORD",
    "CREDENTIALS",
    "AUTH",
    "BEARER",
)


def _redact_env_value(name: str, value: str) -> str:
    """Return *value* if not sensitive, else a length-only marker.

    Sensitive detection is by substring match on the env-var *name*. The
    redacted form preserves length for diffability without leaking content.
    """
    upper = name.upper()
    if any(marker in upper for marker in _SENSITIVE_ENV_NAME_MARKERS):
        return f"<redacted len={len(value)}>"
    return value


def write_invocation_sidecar(
    *,
    target: Path,
    argv: list[str] | tuple[str, ...],
    env: dict[str, str] | None,
    cwd: Path | None,
    metadata: dict[str, str] | None = None,
) -> None:
    """Write a JSON sidecar capturing how a subprocess is being invoked.

    Public helper used both by :class:`LocalBackend.launch` (sidecar next
    to every per-attempt session log) and by
    :func:`metaproc.dispatch.pool_dispatch.probe_credential` (sidecar for
    each pre-flight probe). Goal: a reproducible, by-construction record
    of how each adapter subprocess was actually invoked, so we can diff
    laptop vs cloud-Batch invocations and detect drift over time.

    *target* is the full sidecar path (caller decides naming + location).
    *env* is the per-process env override; merged on top of ``os.environ``
    for the snapshot, then sensitive values are redacted by name. *cwd*
    is recorded as a string. Best-effort: failures never block the caller.
    """
    try:
        env_view: dict[str, str] = dict(os.environ)
        if env is not None:
            env_view.update(env)
        sanitized_env = {name: _redact_env_value(name, val) for name, val in env_view.items()}
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "argv": list(argv),
            "cwd": str(cwd) if cwd else None,
            "env_redacted": sanitized_env,
            "metadata": dict(metadata) if metadata else {},
        }
        target.write_text(json.dumps(payload, indent=2, sort_keys=True))
    except Exception:  # noqa: BLE001 — diagnostic must never block the caller
        log.debug("invocation sidecar write failed (target=%s)", target, exc_info=True)


def _write_invocation_sidecar(prepared: PreparedLaunch) -> None:
    """Internal wrapper for :class:`LocalBackend.launch`.

    Computes the canonical per-attempt sidecar path
    (``<log_path>.invocation.json``) and delegates to
    :func:`write_invocation_sidecar`.
    """
    if prepared.log_path is None:
        return
    target = prepared.log_path.with_suffix(prepared.log_path.suffix + ".invocation.json")
    metadata: dict[str, str] = {"log_path": str(prepared.log_path)}
    if prepared.metadata:
        metadata.update(prepared.metadata)
    write_invocation_sidecar(
        target=target,
        argv=list(prepared.command),
        env=prepared.env,
        cwd=prepared.cwd,
        metadata=metadata,
    )


# ── Value types (frozen dataclasses — never serialized to YAML) ──


@dataclass(frozen=True)
class PreparedLaunch:
    """Concrete spawn inputs resolved immediately before launch.

    The ``metadata`` dict carries backend-specific context that cloud
    backends need but ``LocalBackend`` ignores.  Common keys used by
    cloud backends include ``run_id``, ``item_id``, ``variant``,
    ``container_image``, and ``run_branch``.
    """

    command: tuple[str, ...]
    env: dict[str, str] | None = None
    cwd: Path | None = None
    log_path: Path | None = None
    filter_log: bool = False
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class LaunchHandle:
    """Opaque handle returned by a backend on launch.

    ``pid`` is set by local backends; ``external_id`` is set by cloud
    backends (e.g., a GCP Batch task name).  Both can be set when a
    cloud backend also tracks a local PID.  ``metadata`` carries
    backend-specific state needed for poll/kill/health (e.g., GCP
    project, region, job name).
    """

    pid: int | None = None
    external_id: str | None = None
    backend_name: str = "local"
    metadata: dict[str, str] | None = None
    # Internal: the asyncio subprocess (LocalBackend only).
    _process: asyncio.subprocess.Process | None = field(default=None, repr=False, compare=False)
    _filter_thread: threading.Thread | None = field(default=None, repr=False, compare=False)

    def join_filter_thread(self, timeout: float | None = None) -> None:
        """Allow backends with log-filter threads to flush buffered output."""

        if self._filter_thread is not None:
            self._filter_thread.join(timeout=timeout)


@dataclass(frozen=True)
class HealthMetrics:
    """Point-in-time health snapshot for a running process."""

    rss_bytes: int | None = None
    descendants: int | None = None
    log_bytes: int | None = None


# ── LaunchBackend protocol ──────────────────────────────────────


class LaunchBackend(Protocol):
    """Abstraction for process lifecycle management."""

    @property
    def name(self) -> str: ...

    async def launch(
        self,
        prepared: PreparedLaunch,
        label: str = "",
    ) -> LaunchHandle: ...

    async def poll(self, handle: LaunchHandle) -> int | None: ...

    async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None: ...

    async def health(self, handle: LaunchHandle) -> HealthMetrics | None: ...

    async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str: ...


# ── LocalBackend ────────────────────────────────────────────────


class LocalBackend:
    """Launch and manage local subprocesses via asyncio + psutil."""

    @property
    def name(self) -> str:
        return "local"

    async def launch(
        self,
        prepared: PreparedLaunch,
        label: str = "",
    ) -> LaunchHandle:
        """Spawn a subprocess in its own process group."""
        env: dict[str, str] | None = None
        if prepared.env is not None:
            env = {**os.environ, **prepared.env}

        log_file = None
        filter_thread: threading.Thread | None = None
        read_fd: int | None = None
        stdout_target: int | None = asyncio.subprocess.DEVNULL  # pyright: ignore[reportAssignmentType]
        stderr_target: int = asyncio.subprocess.DEVNULL  # pyright: ignore[reportAssignmentType]

        if prepared.log_path is not None:
            prepared.log_path.parent.mkdir(parents=True, exist_ok=True)
            if prepared.filter_log:
                # Create an OS pipe: subprocess writes to write_fd, filter thread
                # reads from read_fd.  This avoids asyncio StreamReader which is
                # not compatible with the synchronous filter thread.
                read_fd, write_fd = os.pipe()
                stdout_target = write_fd
                stderr_target = asyncio.subprocess.STDOUT
            else:
                log_file = open(prepared.log_path, "w")  # noqa: SIM115
                stdout_target = log_file.fileno()
                stderr_target = asyncio.subprocess.STDOUT

        kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True

        # Write the invocation sidecar BEFORE spawning so we capture the
        # exact argv + env that's about to run, even if the spawn fails.
        _write_invocation_sidecar(prepared)

        try:
            proc = await asyncio.create_subprocess_exec(
                *prepared.command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=stdout_target,
                stderr=stderr_target,
                cwd=str(prepared.cwd) if prepared.cwd else None,
                env=env,
                **kwargs,
            )
        except Exception:
            if log_file is not None:
                log_file.close()
            if read_fd is not None:
                os.close(read_fd)
                os.close(write_fd)  # pyright: ignore[reportPossiblyUnboundVariable]
            raise

        if prepared.filter_log and read_fd is not None and prepared.log_path is not None:
            from metaproc.engine.runtime import (  # noqa: PLC0415 -- pre-existing local import; needs review
                start_log_filter_thread,
            )

            # Close our copy of the write end — the subprocess owns it now.
            os.close(write_fd)  # pyright: ignore[reportPossiblyUnboundVariable]
            pipe_reader = os.fdopen(read_fd, "rb")
            log_file_for_filter = open(prepared.log_path, "w")  # noqa: SIM115
            filter_thread = start_log_filter_thread(pipe_reader, log_file_for_filter)
        elif log_file is not None:
            # Close our copy of the file descriptor — the subprocess owns it now.
            log_file.close()

        return LaunchHandle(
            pid=proc.pid,
            backend_name="local",
            _process=proc,
            _filter_thread=filter_thread,
        )

    async def poll(self, handle: LaunchHandle) -> int | None:
        """Return exit code if the process has exited, else None."""
        proc = handle._process  # noqa: SLF001
        if proc is None:
            return -1
        if proc.returncode is not None:
            exit_code = proc.returncode
            await self.kill(handle)
            return exit_code
        # returncode is only set after wait() completes; use a zero-timeout
        # wait to check without blocking.
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.01)
        except TimeoutError:
            return None
        exit_code = proc.returncode
        if exit_code is not None:
            await self.kill(handle)
        return exit_code

    async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
        """Kill the process group: SIGTERM → wait(5s) → SIGKILL."""
        proc = handle._process  # noqa: SLF001
        pid = handle.pid
        if proc is None or pid is None:
            return

        effective_sig = sig or signal.SIGTERM
        if sys.platform == "win32":
            if proc.returncode is not None:
                return
            if effective_sig == signal.SIGKILL:
                proc.kill()
            else:
                proc.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=_PROCESS_KILL_WAIT_S)
            return

        # LocalBackend launches a new session, so the leader PID is also the stable
        # process-group ID. Keep that ID after the leader exits: a descendant may
        # ignore SIGTERM and remain in the group after its parent has been reaped.
        pgid = pid
        try:
            os.killpg(pgid, effective_sig)
        except ProcessLookupError:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=_PROCESS_KILL_WAIT_S)
            return

        if effective_sig != signal.SIGKILL and not await _wait_for_process_group_exit(
            pgid,
            proc,
            timeout_s=_PROCESS_TERMINATION_GRACE_S,
        ):
            log.warning("Process group %d survived SIGTERM; sending SIGKILL", pgid)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgid, signal.SIGKILL)

        if not await _wait_for_process_group_exit(
            pgid,
            proc,
            timeout_s=_PROCESS_KILL_WAIT_S,
        ):
            msg = f"Process group {pgid} still has live members after SIGKILL"
            raise RuntimeError(msg)

    async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
        """Collect RSS (tree total), descendant count, and log file size."""
        pid = handle.pid
        if pid is None:
            return None

        rss_bytes: int | None = None
        descendants: int | None = None
        log_bytes: int | None = None

        try:
            p = psutil.Process(pid)
            # Sum RSS across entire process tree.
            rss_bytes = p.memory_info().rss
            children = p.children(recursive=True)
            descendants = len(children)
            for child in children:
                with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                    rss_bytes += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

        # Log file size — read from the prepared launch's log_path.
        # The handle doesn't carry the log path, so we check if it was
        # stored in metadata. For now, return None for log_bytes and let
        # the monitor supply it from ProcessConfig.
        return HealthMetrics(
            rss_bytes=rss_bytes,
            descendants=descendants,
            log_bytes=log_bytes,
        )

    async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
        """Read last N lines from the process log (if available)."""
        # The log path is not stored on the handle — the caller (monitor)
        # should read it from ProcessConfig.log_path directly.
        return ""


def _process_group_exists(pgid: int) -> bool:
    """Return whether a POSIX process group still has members."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


async def _wait_for_process_group_exit(
    pgid: int,
    process: asyncio.subprocess.Process,
    *,
    timeout_s: float,
) -> bool:
    """Reap the leader while waiting for every process-group member to exit."""
    deadline = time.monotonic() + timeout_s
    while True:
        if process.returncode is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=0.01)
        if not _process_group_exists(pgid):
            return True
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            return False
        await asyncio.sleep(min(_PROCESS_GROUP_EXIT_POLL_INTERVAL_S, remaining_s))


async def _await_task_completion[Result](task: asyncio.Task[Result]) -> Result:
    """Drain an ownership cleanup task even if cancellation is requested again."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def cancel_launch(backend: LaunchBackend, handle: LaunchHandle) -> None:
    """Kill one launch and wait until backend cleanup has finished."""

    async def _kill_and_wait() -> None:
        await backend.kill(handle)
        while await backend.poll(handle) is None:
            await asyncio.sleep(_PROCESS_GROUP_EXIT_POLL_INTERVAL_S)

    kill_task = asyncio.create_task(_kill_and_wait())
    await _await_task_completion(kill_task)


async def launch_and_supervise(
    backend: LaunchBackend,
    prepared: PreparedLaunch,
    *,
    timeout_s: float | None,
    poll_interval_s: float = _PROCESS_POLL_INTERVAL_S,
    on_cancel: Callable[[], None] | None = None,
) -> int:
    """Launch one process and retain ownership through exit, timeout, or cancellation.

    This is the small scalar counterpart to RunPool supervision. It deliberately uses
    the same backend lifecycle instead of introducing another subprocess wrapper.
    """
    if poll_interval_s <= 0:
        raise ValueError("poll_interval_s must be greater than zero")

    launch_task = asyncio.create_task(backend.launch(prepared))
    try:
        handle = await asyncio.shield(launch_task)
    except asyncio.CancelledError as cancelled:
        if on_cancel is not None:
            on_cancel()
        try:
            handle = await _await_task_completion(launch_task)
        except BaseException:
            raise cancelled from None
        await cancel_launch(backend, handle)
        raise cancelled

    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    try:
        while True:
            exit_code = await backend.poll(handle)
            if exit_code is not None:
                return exit_code
            if deadline is not None:
                remaining_s = deadline - time.monotonic()
                if remaining_s <= 0:
                    raise TimeoutError
                await asyncio.sleep(min(poll_interval_s, remaining_s))
            else:
                await asyncio.sleep(poll_interval_s)
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError) and on_cancel is not None:
            on_cancel()
        await cancel_launch(backend, handle)
        raise
    finally:
        if handle._filter_thread is not None:  # noqa: SLF001 - lifecycle owned here
            join_task = asyncio.create_task(asyncio.to_thread(handle.join_filter_thread, 5.0))
            await _await_task_completion(join_task)
            if handle._filter_thread.is_alive():  # noqa: SLF001 - lifecycle owned here
                log.warning("Log filter thread did not exit after supervised launch ended")


# ── Utilities ───────────────────────────────────────────────────


def get_log_size(log_path: Path | None) -> int | None:
    """Return log file size in bytes, or None if not available."""
    if log_path is None:
        return None
    try:
        return log_path.stat().st_size
    except OSError:
        return None


def read_log_tail_sync(log_path: Path | None, lines: int = 50) -> str:
    """Read the last *lines* of a log file synchronously."""
    if log_path is None:
        return ""
    try:
        text = log_path.read_text(errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except OSError:
        return ""
