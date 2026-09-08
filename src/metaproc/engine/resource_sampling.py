"""Runtime CPU and RSS sampling for one executing step or task."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

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
) -> Generator[PsutilSampler, None, None]:
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


def _process_create_time(pid: int) -> float | None:
    """Return a process identity timestamp when it can be observed."""
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
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
        except (ProcessLookupError, PermissionError):
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
