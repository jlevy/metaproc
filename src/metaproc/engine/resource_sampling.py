"""Runtime CPU and RSS sampling for one executing step or task."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, Literal, NoReturn, TextIO

import psutil

from metaproc.logutil.resource_events import ResourceEventLogger
from metaproc.models.resources import HierarchyRef, SourceRef
from metaproc.osutils.psutil_sampler import PsutilSampler
from metaproc.paths import LOGS_DIR, RESOURCE_EVENTS_FILE, run_config_file

log = logging.getLogger(__name__)

# How long supervised commands get to exit gracefully before escalation.
_PROCESS_TERMINATION_GRACE_S = 5.0
# How long Metaproc waits for the process group after an uncatchable kill signal.
_PROCESS_KILL_WAIT_S = 5.0
# Responsiveness bound for cancellation and exited-leader detection.
_COMMAND_POLL_INTERVAL_S = 0.1
# Poll cadence while waiting for a signalled process group to disappear.
_PROCESS_GROUP_EXIT_POLL_INTERVAL_S = 0.05


@dataclass(frozen=True)
class _SamplingTarget:
    run_dir: Path
    run_id: str
    step_node_id: str


def _sampling_target(run_dir: Path, run_id: str, step_node_id: str) -> _SamplingTarget:
    """Resolve composite children back to the root resource ledger hierarchy."""
    root_run_dir = next(
        (
            candidate
            for candidate in (run_dir, *run_dir.parents)
            if run_config_file(candidate).is_file()
        ),
        run_dir,
    )
    subgraph_parts = run_dir.relative_to(root_run_dir).parts
    if not subgraph_parts:
        return _SamplingTarget(run_dir=run_dir, run_id=run_id, step_node_id=step_node_id)

    nested_run_suffix = "/" + "/".join(subgraph_parts)
    root_run_id = run_id.removesuffix(nested_run_suffix)
    return _SamplingTarget(
        run_dir=root_run_dir,
        run_id=root_run_id,
        step_node_id="::".join((*subgraph_parts, step_node_id)),
    )


@contextmanager
def sample_step_resources(
    *,
    run_dir: Path,
    run_id: str,
    step_node_id: str,
    item_key: str | None = None,
    pid: int | None = None,
) -> Generator[PsutilSampler]:
    """Persist psutil samples under the deepest known step hierarchy."""
    target = _sampling_target(run_dir, run_id, step_node_id)
    event_path = target.run_dir / LOGS_DIR / RESOURCE_EVENTS_FILE
    source = SourceRef(
        kind="psutil_sampler",
        path=event_path.relative_to(target.run_dir).as_posix(),
    )
    hierarchy = HierarchyRef(
        run_id=target.run_id,
        step_node_id=target.step_node_id,
        item_key=item_key,
    )
    with ExitStack() as stack:
        try:
            logger = stack.enter_context(ResourceEventLogger(event_path))
        except OSError:
            logger = None
            log.debug("Resource sampler could not open the event log", exc_info=True)

        sampler = stack.enter_context(
            PsutilSampler(
                hierarchy=hierarchy,
                source=source,
                logger=logger,
                pid=pid,
                exclude_preexisting_children=pid is None,
            )
        )
        yield sampler


class _CallerSignal(BaseException):
    """A SIGTERM or SIGHUP the caller received while an owned command ran.

    Raised from the launcher's signal handler so the command's cleanup runs. It never
    escapes :func:`run_command_in_process_group`, which re-delivers the signal instead.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@dataclass
class _CallerSignalWatch:
    """Termination signals seen while one owned command runs."""

    received: list[int] = field(default_factory=list)
    #: The first signal raises so cleanup starts; later ones only record, so a second
    #: SIGTERM cannot cut the group cleanup short. The launcher clears this itself
    #: before cleanup that another failure started.
    raising: bool = True

    def handle(self, signum: int, frame: FrameType | None) -> None:
        del frame
        self.received.append(signum)
        if self.raising:
            self.raising = False
            raise _CallerSignal(signum)


@contextmanager
def _caller_signals_end_the_command() -> Generator[_CallerSignalWatch]:
    """Make SIGTERM and SIGHUP to the caller end the owned command, not orphan it.

    The command's session is not the caller's, so a signal to the caller's process
    group (a dropped terminal's SIGHUP, ``kill``, GNU ``timeout``, a CI cancel) no
    longer reaches the command, and Python's default disposition would then die
    without running the launcher's cleanup. For the call's duration the two signals
    raise instead, so the group is terminated; once the handlers are restored the
    first signal is re-delivered under its default disposition, and the caller dies
    by it as it did when the command shared its group. Only default dispositions are
    replaced: an ignored SIGHUP (``nohup``) stays ignored, and a caller's own handler
    keeps its policy. Off the main thread, where handlers cannot be installed, and on
    Windows, where SIGHUP does not exist, nothing is installed and the command runs
    as before. SIGKILL cannot be handled and still leaves the tree behind.
    """
    watch = _CallerSignalWatch()
    if sys.platform == "win32" or threading.current_thread() is not threading.main_thread():
        yield watch
        return
    previous: dict[int, Any] = {}
    for signum in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(signum) == signal.SIG_DFL:
            previous[signum] = signal.signal(signum, watch.handle)
    try:
        try:
            yield watch
        except _CallerSignal:
            pass  # re-delivered below, once the handlers are restored
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if watch.received:
            _die_by_signal(watch.received[0])


def _die_by_signal(signum: int) -> NoReturn:
    """Re-deliver *signum* to this process under its restored default disposition."""
    os.kill(os.getpid(), signum)
    # Reached only if the signal did not terminate the process.
    raise SystemExit(128 + signum)


def _communicate_until_leader_exits(
    process: subprocess.Popen[str],
    *,
    timeout: float | None,
    leader_create_time: float | None,
) -> tuple[str, str]:
    """Collect the command's output, bounded by *timeout* on the leader, not its tree.

    ``communicate`` returns only once stdout and stderr reach EOF, which a descendant
    that inherited the pipes postpones past the leader's exit. The leader is polled
    instead: once it has exited, the survivors are terminated and the output drained.
    A timeout keeps ``subprocess.run``'s exception, with the caller's timeout and the
    partial output.
    """
    started = time.monotonic()
    while True:
        slice_s = _COMMAND_POLL_INTERVAL_S
        if timeout is not None:
            slice_s = max(0.0, min(slice_s, started + timeout - time.monotonic()))
        try:
            return process.communicate(timeout=slice_s)
        except subprocess.TimeoutExpired as exc:
            if process.poll() is not None:
                _terminate_process_tree(
                    process,
                    leader_create_time=leader_create_time,
                    ownership_observed_until=time.time(),
                )
                return process.communicate(timeout=_PROCESS_KILL_WAIT_S)
            if timeout is not None and time.monotonic() - started >= timeout:
                exc.timeout = timeout
                raise


def run_command_in_process_group(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path | str | None = None,
    timeout: float | None = None,
    stdin: int | None = None,
    stdout: TextIO | int | None = None,
    stderr: int | None = None,
    capture_output: bool = False,
    text: Literal[True] = True,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a text command as an owned process group, collected like ``subprocess.run``.

    Every agent command Metaproc runs in the foreground goes through here, whichever
    adapter built it: ``auth-check --live``, ``probe-tool-use``, and ``run-step
    --wait`` (except its pi-cli log-filter branch, which still launches with a bare
    ``Popen``). An agent CLI is never one process. It runs tool calls and MCP servers
    as children, and an adapter's ``build_command`` may wrap it in a shell (Codex
    redirects the prompt into ``exec``; Gemini keeps ``sh`` alive over a
    ``cat | gemini`` pipeline). ``subprocess.run`` signals only the leader on timeout
    or interrupt, so the rest of that tree survives it. Here the child starts its own
    session, which makes the leader PID the group ID, and the group is cleaned up
    through the same identity-fenced owner as sampled code steps:

    - on timeout and on ``KeyboardInterrupt``, before the exception propagates;
    - on SIGTERM and SIGHUP to the caller, which the command's session would not
      otherwise receive (:func:`_caller_signals_end_the_command`); the caller then
      dies by the signal as before. SIGKILL cannot be handled and leaves the tree
      behind;
    - on leader exit, including when a descendant still holds the leader's stdout or
      stderr pipe, which would otherwise keep ``communicate`` waiting until the
      timeout (:func:`_communicate_until_leader_exits`).

    When the group cannot be verified as this command's, the leader alone is killed,
    as ``subprocess.run`` would: it is this process's unreaped child, so its PID is
    not recyclable. Detached and pooled launches (``launch_step``, ``LocalBackend``)
    hold the same contract; the developer guide states it under "Launch and
    Cancellation Contracts". A call site therefore never selects a launcher by
    adapter type.

    Unlike :func:`run_sampled_step_command`, nothing is written to the run's resource
    ledger. File-directed stdout retains the child's raw bytes. Windows retains
    leader-only cleanup.
    """
    if capture_output:
        if stdout is not None or stderr is not None:
            raise ValueError("stdout and stderr cannot be used with capture_output")
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
    args = list(command)
    with _caller_signals_end_the_command() as caller_signals:
        process = subprocess.Popen(
            args,
            env=env,
            cwd=cwd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=text,
            start_new_session=sys.platform != "win32",
        )
        leader_create_time = _process_create_time(process.pid)
        try:
            try:
                output, errors = _communicate_until_leader_exits(
                    process, timeout=timeout, leader_create_time=leader_create_time
                )
            except BaseException:
                # A caller signal arriving now is recorded and re-delivered after
                # cleanup; raising it here would cut the group cleanup short.
                caller_signals.raising = False
                try:
                    _terminate_process_tree(
                        process,
                        leader_create_time=leader_create_time,
                        ownership_observed_until=time.time(),
                    )
                    # Cleanup can refuse an unverifiable owner; never turn the original
                    # timeout or interrupt into an unbounded drain/context-manager wait.
                    process.communicate(timeout=_PROCESS_KILL_WAIT_S)
                except Exception:
                    log.exception("Could not finish interrupted command cleanup")
                raise
            caller_signals.raising = False
            try:
                _terminate_process_tree(
                    process,
                    leader_create_time=leader_create_time,
                    ownership_observed_until=time.time(),
                )
            except Exception:
                log.exception("Could not finish completed command cleanup")
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        log.exception("Could not close an owned command pipe")
    completed = subprocess.CompletedProcess(args, process.returncode, output, errors)
    if check:
        completed.check_returncode()
    return completed


def run_sampled_step_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    run_dir: Path,
    run_id: str,
    step_node_id: str,
    item_key: str | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a code-step command while sampling only its process tree."""
    args = list(command)
    with subprocess.Popen(
        args,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=sys.platform != "win32",
    ) as process:
        leader_create_time = _process_create_time(process.pid)
        with sample_step_resources(
            run_dir=run_dir,
            run_id=run_id,
            step_node_id=step_node_id,
            item_key=item_key,
            pid=process.pid,
        ):
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=_COMMAND_POLL_INTERVAL_S)
                    _terminate_process_tree(
                        process,
                        leader_create_time=leader_create_time,
                        ownership_observed_until=time.time(),
                    )
                    break
                except subprocess.TimeoutExpired:
                    if cancel_requested is not None and cancel_requested():
                        _terminate_process_tree(
                            process,
                            leader_create_time=leader_create_time,
                            ownership_observed_until=time.time(),
                        )
                        stdout, stderr = process.communicate()
                        raise asyncio.CancelledError from None
                    if process.poll() is not None:
                        _terminate_process_tree(
                            process,
                            leader_create_time=leader_create_time,
                            ownership_observed_until=time.time(),
                        )
                        stdout, stderr = process.communicate()
                        break

    completed = subprocess.CompletedProcess(
        args=args,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    completed.check_returncode()
    return completed


def _terminate_process_tree(
    process: subprocess.Popen[str],
    *,
    leader_create_time: float | None = None,
    ownership_observed_until: float | None = None,
) -> None:
    """Best-effort terminate a command's isolated process group.

    Cleanup failure is logged; it never changes an already-observed command result.
    """
    if sys.platform == "win32":
        try:
            if process.poll() is not None:
                return
            process.terminate()
            try:
                process.wait(timeout=_PROCESS_TERMINATION_GRACE_S)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=_PROCESS_KILL_WAIT_S)
                except subprocess.TimeoutExpired:
                    log.error("Command process %d survived termination", process.pid)
            return
        except OSError:
            log.exception("Could not terminate command process %d", process.pid)
            return

    # start_new_session=True makes the leader PID the stable process-group ID.
    pgid = process.pid
    if not _owned_command_group_exists(
        process,
        leader_create_time=leader_create_time,
        ownership_observed_until=ownership_observed_until,
    ):
        _kill_unverifiable_leader(process)
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    except PermissionError:
        log.error("Permission denied signalling command process group %d", pgid)
        return
    if _wait_for_process_group_exit(
        process,
        timeout_s=_PROCESS_TERMINATION_GRACE_S,
        leader_create_time=leader_create_time,
        ownership_observed_until=ownership_observed_until,
    ):
        return
    log.warning("Command process group %d survived SIGTERM; sending SIGKILL", pgid)
    if not _owned_command_group_exists(
        process,
        leader_create_time=leader_create_time,
        ownership_observed_until=ownership_observed_until,
    ):
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        process.wait()
        return
    except PermissionError:
        log.error("Permission denied sending SIGKILL to command process group %d", pgid)
        return
    if not _wait_for_process_group_exit(
        process,
        timeout_s=_PROCESS_KILL_WAIT_S,
        leader_create_time=leader_create_time,
        ownership_observed_until=ownership_observed_until,
    ):
        log.error("Command process group %d still has live members after SIGKILL", pgid)


def _kill_unverifiable_leader(process: subprocess.Popen[str]) -> None:
    """Kill a still-running leader whose group cannot be verified as this command's.

    The group is never signalled without proof of ownership, but the leader needs
    none: it is this process's unreaped child, so its PID cannot have been recycled,
    and ``subprocess.run`` kills it in the same situation. Its descendants stay out of
    reach without the group.
    """
    if process.poll() is not None:
        return
    log.warning("Command process group %d cannot be verified; killing its leader only", process.pid)
    try:
        process.kill()
    except OSError:
        log.exception("Could not kill command process %d", process.pid)
        return
    try:
        process.wait(timeout=_PROCESS_KILL_WAIT_S)
    except subprocess.TimeoutExpired:
        log.error("Command process %d survived SIGKILL", process.pid)


def _process_create_time(pid: int) -> float | None:
    """Return a process identity timestamp when it can be observed."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess:
        return None


def _owned_command_group_exists(
    process: subprocess.Popen[str],
    *,
    leader_create_time: float | None,
    ownership_observed_until: float | None,
) -> bool:
    """Return whether a live group member still proves command ownership."""
    pgid = process.pid
    if leader_create_time is None:
        return False
    current_leader_create_time = _process_create_time(pgid)
    if current_leader_create_time is not None:
        if current_leader_create_time != leader_create_time:
            return False
        if process.poll() is None:
            return True
    if ownership_observed_until is None:
        return False
    for candidate in psutil.process_iter(["pid", "create_time"]):
        candidate_pid = candidate.info.get("pid")
        create_time = candidate.info.get("create_time")
        if (
            not isinstance(candidate_pid, int)
            or candidate_pid == pgid
            or not isinstance(create_time, (int, float))
            or create_time < leader_create_time
            or create_time > ownership_observed_until
        ):
            continue
        try:
            if os.getpgid(candidate_pid) == pgid:
                return True
        except ProcessLookupError, PermissionError:
            continue
    return False


def _wait_for_process_group_exit(
    process: subprocess.Popen[str],
    *,
    timeout_s: float,
    leader_create_time: float | None,
    ownership_observed_until: float | None,
) -> bool:
    """Reap the command leader while waiting for all group members to exit."""
    deadline = time.monotonic() + timeout_s
    while True:
        process.poll()
        if not _owned_command_group_exists(
            process,
            leader_create_time=leader_create_time,
            ownership_observed_until=ownership_observed_until,
        ):
            return True
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            return False
        time.sleep(min(_PROCESS_GROUP_EXIT_POLL_INTERVAL_S, remaining_s))
