"""Kill a running pool — write sentinel file and terminate child process groups.

This module is self-contained within the runpool package: it reads
``runpool-status.yaml``, writes a sentinel file, and sends OS signals.
No dependencies on metaproc engine, io, or domain logic.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psutil

from metaproc.io import atomic_output_file, new_yaml, read_yaml_file
from metaproc.paths import POOL_KILL_SENTINEL_FILE, POOL_STATUS_FILE, STATE_DIR
from metaproc.runpool.status import ProcessStatus, is_pool_alive, read_status

log = logging.getLogger(__name__)


@dataclass
class KilledProcess:
    """Result of attempting to kill one child process."""

    pid: int
    label: str
    status: str  # "killed", "already_exited", "permission_denied"


@dataclass
class KillResult:
    """Summary returned by `kill_pool()`."""

    pool_id: str
    requested_at: datetime
    signal_sent: str
    drain: bool
    killed_processes: list[KilledProcess] = field(default_factory=list)
    drained_pending: int = 0
    pool_alive_after: bool = False


def _write_sentinel(
    state_dir: Path,
    *,
    reason: str,
    sig: str,
    variant: str | None,
    drain: bool,
) -> Path:
    """Atomically write the kill sentinel file."""
    data = {
        "requested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "reason": reason,
        "signal": sig,
        "variant": variant,
        "drain": drain,
    }
    # Operators expect every sentinel field present (including `variant: null`
    # when no variant filter was set); disable None suppression.
    buf = io.StringIO()
    new_yaml(suppress_vals=lambda _v: False).dump(data, buf)
    content = buf.getvalue()
    sentinel_path = state_dir / POOL_KILL_SENTINEL_FILE

    with atomic_output_file(sentinel_path) as tmp:
        Path(tmp).write_text(content)

    return sentinel_path


def _kill_process_group(pid: int, sig: int) -> str:
    """Send a signal to a process group. Returns status string."""
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, sig)
        return "killed"
    except ProcessLookupError:
        return "already_exited"
    except PermissionError:
        return "permission_denied"


def _kill_with_escalation(pid: int) -> str:
    """SIGTERM → wait(3s) → SIGKILL if still alive."""
    status = _kill_process_group(pid, signal.SIGTERM)
    if status != "killed":
        return status

    # Brief wait for graceful exit, then escalate.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)  # Check if still alive.
        except ProcessLookupError:
            return "killed"
        except PermissionError:
            return "killed"
        time.sleep(0.2)

    # Still alive — escalate.
    log.warning("Process %d did not exit after SIGTERM; sending SIGKILL", pid)
    _kill_process_group(pid, signal.SIGKILL)
    return "killed"


def kill_pool(
    run_dir: Path,
    *,
    variant: str | None = None,
    sig: str = "SIGTERM",
    drain: bool = False,
    force: bool = False,
) -> KillResult:
    """
    Kill a running pool by writing a sentinel and terminating child processes.

    The sentinel file tells the pool's monitor loop to shut down cleanly.
    Direct process kills provide immediate relief even if the pool is sluggish.

    `force` sends SIGKILL immediately (no SIGTERM grace period).
    `drain` writes the sentinel but does not kill running processes.
    """
    now = datetime.now(UTC)

    # Locate and read pool status.
    status_path = run_dir / STATE_DIR / POOL_STATUS_FILE
    if not status_path.exists():
        # Fall back to legacy location (run_dir / filename) for compat.
        status_path = run_dir / POOL_STATUS_FILE
    if not status_path.exists():
        raise FileNotFoundError(f"No pool status file at {run_dir / STATE_DIR / POOL_STATUS_FILE}")

    pool_status = read_status(status_path)

    if not is_pool_alive(pool_status):
        raise RuntimeError(f"Pool {pool_status.pool_id} (pid {pool_status.pid}) is not running")

    # Write sentinel file.
    effective_sig = "SIGKILL" if force else sig
    _write_sentinel(
        run_dir / STATE_DIR,
        reason="user_requested",
        sig=effective_sig,
        variant=variant,
        drain=drain,
    )
    log.info("Kill sentinel written to %s", run_dir / POOL_KILL_SENTINEL_FILE)

    result = KillResult(
        pool_id=pool_status.pool_id,
        requested_at=now,
        signal_sent=effective_sig,
        drain=drain,
        drained_pending=pool_status.pending_count,
    )

    if drain:
        # Drain mode — don't kill processes, just let the pool wind down.
        result.pool_alive_after = True
        return result

    # Kill child process groups directly.
    os_sig = signal.SIGKILL if force else signal.SIGTERM
    targets = _select_targets(pool_status.processes, variant)

    for proc_status in targets:
        if proc_status.pid is None:
            continue
        if force:
            status = _kill_process_group(proc_status.pid, os_sig)
        else:
            status = _kill_with_escalation(proc_status.pid)
        result.killed_processes.append(
            KilledProcess(
                pid=proc_status.pid,
                label=proc_status.label,
                status=status,
            )
        )

    # Send SIGTERM to the pool process itself as a backup.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pool_status.pid, signal.SIGTERM)

    # Brief poll to check if pool exited.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not is_pool_alive(pool_status):
            break
        time.sleep(0.5)

    result.pool_alive_after = is_pool_alive(pool_status)
    return result


def _select_targets(processes: list[ProcessStatus], variant: str | None) -> list[ProcessStatus]:
    """Filter to running processes, optionally by variant label prefix."""
    running = [p for p in processes if p.status == "running"]
    if variant is None:
        return running
    return [p for p in running if p.label.startswith(variant)]


def read_sentinel(run_dir: Path) -> dict[str, object] | None:
    """Read the kill sentinel file if it exists. Returns parsed YAML or None."""
    sentinel_path = run_dir / STATE_DIR / POOL_KILL_SENTINEL_FILE
    if not sentinel_path.exists():
        # Fall back to legacy location.
        sentinel_path = run_dir / POOL_KILL_SENTINEL_FILE
    if not sentinel_path.exists():
        return None
    return read_yaml_file(sentinel_path)


def reap_subprocess_tree(parent_pid: int | None = None) -> dict[str, int]:
    """SIGKILL every descendant of the given process (default: current process).

    Walks the process tree using psutil and sends SIGKILL to every descendant
    in one pass. Used as a hard-stop subprocess cleanup on orchestrator exit
    paths (signal handlers, atexit hooks) that the cooperative
    ``RunPool.shutdown()`` does not run for — i.e., when the orchestrator is
    SIGTERM'd by an external operator and Python's default SIGTERM behavior
    skips cleanup.

    This is a complement to ``RunPool.shutdown()``, not a replacement: the
    cooperative shutdown writes status, releases leases, and tears down
    monitors cleanly. This function is the safety net that ensures no
    adapter-CLI / adapter-cli / vite-node grandchildren survive the orchestrator
    process tree on an unclean exit.

    Returns a dict with counts of killed / not-found / permission-denied
    descendants, for logging by the caller.

    See this regression + the incident analysis for the
    motivating incident: 25 orphan adapter-cli processes from SIGTERM'd T1
    orchestrators consumed 30-90% CPU each for 6+ hours on a single batch.
    """

    target_pid = parent_pid if parent_pid is not None else os.getpid()
    counts = {"killed": 0, "not_found": 0, "permission_denied": 0, "errors": 0}

    try:
        parent = psutil.Process(target_pid)
    except psutil.NoSuchProcess:
        counts["not_found"] += 1
        return counts

    try:
        # recursive=True returns the full descendant tree (children of
        # children of children…). This is what we need — orphan adapter-cli /
        # claude --print / etc. are typically grandchildren.
        descendants = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        counts["errors"] += 1
        return counts

    # Kill descendants depth-first (children before parents) so the
    # immediate-child processes don't see their grandchildren spawning new
    # work during the kill window.
    descendants.sort(key=lambda p: -len(p.parents()))

    for proc in descendants:
        try:
            proc.kill()  # SIGKILL; no grace period
            counts["killed"] += 1
        except psutil.NoSuchProcess:
            counts["not_found"] += 1
        except psutil.AccessDenied:
            counts["permission_denied"] += 1
        except Exception:
            log.warning("Failed to kill descendant PID %d", proc.pid, exc_info=True)
            counts["errors"] += 1

    return counts


def install_subprocess_reaper_signal_handlers() -> None:
    """Install SIGTERM + SIGINT handlers that reap the subprocess tree on exit.

    Without this, when an operator runs ``kill -TERM <orchestrator_pid>``,
    Python's default SIGTERM behavior is to exit immediately without running
    any cleanup. Any subprocess children spawned by the orchestrator
    (adapter CLIs, adapter-cli, vite-node, etc.) survive as orphans and burn
    CPU until they hit their own timeouts (which can be hours).

    This handler:
    1. Reaps the subprocess tree synchronously (SIGKILL all descendants).
    2. Re-raises the default signal so the orchestrator exits normally.

    Idempotent — calling twice is a no-op (signal handlers are replaced,
    not stacked).

    Call this once at orchestrator startup, BEFORE any subprocess work
    begins. See this regression + the incident analysis.
    """

    def _handler(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        log.warning("Orchestrator received %s; reaping subprocess tree", sig_name)
        counts = reap_subprocess_tree()
        log.warning(
            "Subprocess tree reaped on %s: killed=%d not_found=%d permission_denied=%d errors=%d",
            sig_name,
            counts["killed"],
            counts["not_found"],
            counts["permission_denied"],
            counts["errors"],
        )
        # Re-raise with default handler so the orchestrator exits.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
