"""Runtime CPU and RSS sampling for one executing step or task."""

from __future__ import annotations

import asyncio
import locale
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
from typing import IO, Any, Literal, TextIO

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


#: The caller-directed termination signals the launcher watches. Each would kill the
#: caller under its default disposition without reaching the command's own session.
_CALLER_SIGNALS: tuple[int, ...] = (
    () if sys.platform == "win32" else (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)
)


class _CallerSignal(BaseException):
    """A watched signal the caller received while an owned command ran.

    Raised from the launcher's signal handler so the command's cleanup runs. It never
    escapes :func:`run_command_in_process_group`, which re-delivers the signal instead.
    ``watch`` names the watch whose handler raised it, so a launch nested inside another
    passes an enclosing watch's signal through after its own cleanup.
    """

    def __init__(self, signum: int, watch: _CallerSignalWatch) -> None:
        super().__init__(signum)
        self.signum = signum
        self.watch = watch


@dataclass
class _CallerSignalWatch:
    """Termination signals seen while one owned command runs."""

    received: list[int] = field(default_factory=list)
    #: The first watched signal raises so cleanup starts; later ones only record, so a
    #: second SIGTERM cannot cut the group cleanup short. The launcher clears this
    #: itself, through ``shield_cleanup``, before cleanup that another failure started.
    raising: bool = True
    #: A SIGINT seen while cleanup was shielded; raised once the cleanup is done.
    interrupted: bool = False
    #: False until the launcher is inside the scope that cleans up. A signal before
    #: then (during the fork or the identity read) is only recorded, and ``arm``
    #: raises it, so it can neither leave the child unbound nor go uncleaned.
    armed: bool = False
    #: The caller's signal mask on entry when handlers are installed, or None when
    #: they are not (Windows, or off the main thread).
    previous_mask: frozenset[int] | None = field(default=None, repr=False)
    sigint_handler: Any = field(default=None, repr=False)

    def handle(self, signum: int, frame: FrameType | None) -> None:
        del frame
        self.received.append(signum)
        if self.armed and self.raising:
            self.raising = False
            raise _CallerSignal(signum, self)

    def record_interrupt(self, signum: int, frame: FrameType | None) -> None:
        del signum, frame
        self.interrupted = True

    def arm(self) -> None:
        """Raise watched signals from now on, from inside the scope that cleans up.

        A signal recorded before arming is raised here, so its cleanup still runs.
        """
        self.armed = True
        if self.received and self.raising:
            self.raising = False
            raise _CallerSignal(self.received[0], self)

    def shield_cleanup(self) -> None:
        """Cleanup has begun: record later signals instead of raising, SIGINT included.

        A watched signal is re-delivered, and a SIGINT raised as ``KeyboardInterrupt``,
        once the bounded group cleanup has finished, so neither cuts it short. SIGINT is
        shielded only while Python's own handler holds it; an ignored SIGINT stays so.
        """
        self.raising = False
        if (
            self.previous_mask is not None
            and self.sigint_handler is None
            and signal.getsignal(signal.SIGINT) is signal.default_int_handler
        ):
            self.sigint_handler = signal.signal(signal.SIGINT, self.record_interrupt)


@contextmanager
def _caller_signals_end_the_command() -> Generator[_CallerSignalWatch]:
    """Make SIGTERM, SIGHUP and SIGQUIT to the caller end the owned command, not orphan it.

    The command's session is not the caller's, so a signal to the caller's process
    group (a dropped terminal's SIGHUP, ``kill``, GNU ``timeout``, a CI cancel, the
    terminal's Ctrl-\\) no longer reaches the command, and Python's default disposition
    would then die without running the launcher's cleanup. For the call's duration the
    watched signals raise instead, so the group is terminated; once the handlers are
    restored the first signal is re-delivered under its default disposition, and the
    caller dies by it as it did when the command shared its group. Only default
    dispositions are replaced: an ignored SIGHUP (``nohup``) stays ignored, and a
    caller's own handler keeps its policy.

    The watched signals are blocked only while the handlers go in and come out, so
    none can leave a handler half installed; the caller's own mask is restored around
    the command, so the child inherits that mask rather than one blocking the signals
    its cleanup sends. A signal that lands before the launcher calls ``arm`` inside
    the scope that cleans up (during the fork or the identity read) is recorded, and
    ``arm`` raises it there. A signal that arrives while the handlers come out is
    delivered under the restored disposition once the caller's mask is back. A SIGINT
    during cleanup is recorded and raised afterwards (``shield_cleanup``).

    Not covered: SIGKILL, and SIGSTOP followed by it. Off the main thread, where
    handlers cannot be installed, and on Windows, nothing is armed: the command still
    runs in its own session, so a group-directed signal does not reach it, and a
    caller dying by one orphans it. No call site launches off the main thread.
    """
    watch = _CallerSignalWatch()
    if sys.platform == "win32" or threading.current_thread() is not threading.main_thread():
        yield watch
        return
    previous_mask = frozenset(signal.pthread_sigmask(signal.SIG_BLOCK, _CALLER_SIGNALS))
    watch.previous_mask = previous_mask
    previous_handlers: dict[int, Any] = {}
    try:
        for signum in _CALLER_SIGNALS:
            if signal.getsignal(signum) == signal.SIG_DFL:
                previous_handlers[signum] = signal.signal(signum, watch.handle)
        # A signal that was pending reaches the unarmed handler here and is recorded.
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        try:
            yield watch
        except _CallerSignal as exc:
            if exc.watch is not watch:
                raise  # an enclosing launch's signal; its own watch re-delivers it
    finally:
        # Block again so no handler runs half removed.
        signal.pthread_sigmask(signal.SIG_BLOCK, _CALLER_SIGNALS)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if watch.sigint_handler is not None:
            signal.signal(signal.SIGINT, watch.sigint_handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        _end_with_received_signals(watch)


def _end_with_received_signals(watch: _CallerSignalWatch) -> None:
    """Act on the signals recorded during the command, now that its cleanup is done."""
    if watch.received:
        signum = watch.received[0]
        # Re-deliver under the restored default disposition; the caller dies by it.
        os.kill(os.getpid(), signum)
        # Reached only if the signal did not terminate the process.
        raise SystemExit(128 + signum)
    if watch.interrupted:
        raise KeyboardInterrupt


def _partial_text(stream: IO[str] | None, data: object) -> str | None:
    """Decode a partial pipe read as the text-mode pipe itself would have.

    No pipe gives None, as ``communicate`` does; a pipe nothing was read from gives
    the empty text ``subprocess.run`` returns for it, where ``TimeoutExpired`` carries
    None.
    """
    if stream is None:
        return None
    if isinstance(data, str):
        return data
    if not isinstance(data, bytes):
        return ""
    encoding = getattr(stream, "encoding", None) or locale.getpreferredencoding(False)
    errors = getattr(stream, "errors", None) or "strict"
    return data.decode(encoding, errors).replace("\r\n", "\n").replace("\r", "\n")


def _communicate_until_leader_exits(
    process: subprocess.Popen[str],
    *,
    timeout: float | None,
    leader_create_time: float | None,
) -> tuple[str | None, str | None]:
    """Collect the command's output, bounded by *timeout* on the leader, not its tree.

    ``communicate`` returns only once stdout and stderr reach EOF, which a descendant
    that inherited the pipes postpones past the leader's exit. The leader is polled
    instead: once it has exited, the survivors in its group are terminated and the
    output drained. A holder that left the group (a tool the CLI spawned with its own
    session) is out of reach, so the drain is bounded and the output so far is the
    command's output, as ``subprocess.run`` would have returned it once that process
    exited. A timeout keeps ``subprocess.run``'s exception, with the caller's timeout
    and the partial output.
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
                try:
                    return process.communicate(timeout=_PROCESS_KILL_WAIT_S)
                except subprocess.TimeoutExpired as drain:
                    log.warning(
                        "Command %d exited, but a process outside its group still holds "
                        "its output pipe; returning the output collected so far",
                        process.pid,
                    )
                    return (
                        _partial_text(process.stdout, drain.output),
                        _partial_text(process.stderr, drain.stderr),
                    )
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

    - on timeout and on ``KeyboardInterrupt``, before the exception propagates; a
      second SIGINT during that cleanup is recorded and raised once it is done;
    - on SIGTERM, SIGHUP and SIGQUIT to the caller, which the command's session would
      not otherwise receive (:func:`_caller_signals_end_the_command`); the caller then
      dies by the signal as before. Not covered: SIGKILL, and SIGSTOP followed by it;
    - on leader exit, including when a descendant still holds the leader's stdout or
      stderr pipe, which would otherwise keep ``communicate`` waiting until the
      timeout (:func:`_communicate_until_leader_exits`).

    The group is what the launcher can reach. A tool subprocess the CLI detached into
    its own session (Gemini's and pi's shell tools do) is beyond it and is left to the
    CLI's own signal handling, as it was before. When the group cannot be verified as
    this command's, the leader alone is killed, as ``subprocess.run`` would: it is this
    process's unreaped child, so its PID is not recyclable. Detached and pooled
    launches (``launch_step``, ``LocalBackend``) hold the same contract; the developer
    guide states it under "Launch and Cancellation Contracts". A call site therefore
    never selects a launcher by adapter type. Every call site is on the main thread;
    off it, the signal watch is not armed (see the watch). A launch nested inside
    another is not a use case, but an enclosing watch's signal passes through it
    after its own cleanup.

    Unlike :func:`run_sampled_step_command`, nothing is written to the run's resource
    ledger. File-directed stdout retains the child's raw bytes. Windows retains
    leader-only cleanup and no signal watch.
    """
    if capture_output:
        if stdout is not None or stderr is not None:
            raise ValueError("stdout and stderr cannot be used with capture_output")
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
    args = list(command)
    with _caller_signals_end_the_command() as caller_signals:
        # A watched signal during the fork or the identity read is only recorded: raised
        # here it would leave the child unbound or uncleaned. ``arm`` raises it inside
        # the scope below, which cleans up after it. The signals are not blocked here,
        # since the child would inherit the mask and ignore its cleanup's SIGTERM.
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
                caller_signals.arm()
                output, errors = _communicate_until_leader_exits(
                    process, timeout=timeout, leader_create_time=leader_create_time
                )
            except BaseException:
                # A later signal, SIGINT included, is recorded and acted on after
                # cleanup; raising it here would cut the bounded cleanup short.
                caller_signals.shield_cleanup()
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
            caller_signals.shield_cleanup()
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
