"""metaproc resume-daemon — non-LLM checkpoint-driven re-dispatch loop.

plan-2026-04-21-auth-credential-pool.md §Components 10 (P2c.5).

Polls ``<runs_dir>/*/*/.state/retry_later.yaml`` for pending
checkpoints written by ``--auth-retry-later=signal`` dispatches.
When ``now >= checkpoint.cooling_until_ts`` the daemon shells out
``metaproc run-process --from <step_id>`` with the saved argv +
env snapshot, then archives the checkpoint.

Deliberately dumb: no classification, no judgment, no LLM call. A
~200-line polling daemon is what makes overnight runs actually
complete after quota exhaustion (G15 — code-based resume).

Per-operator scope (OQ19): one daemon per operator, polling their
own RUNS_DIR. Cross-user shared checkpoints are out of scope;
shared RUNS_DIR on NFS with one daemon for the team can be a
future revision (schema is version-1-locked already).
"""

from __future__ import annotations

import json as _json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from metaproc.cli import app, get_output
from metaproc.dispatch.retry_later import (
    RETRY_LATER_EXIT_CODE,
    RetryLaterCheckpoint,
    read_checkpoint,
)
from metaproc.io.mkdir_lock import MkdirLockTimeoutError, acquire_mkdir_lock

log = logging.getLogger(__name__)


def _find_pending_checkpoints(runs_dir: Path) -> list[Path]:
    """Return every ``retry_later.yaml`` under *runs_dir*.

    Skips checkpoints already archived (``retry_later.resumed.yaml`` /
    ``retry_later.exhausted.yaml``). Uses glob so the daemon's cost
    stays O(n runs × n processes) rather than walking the whole
    artifact tree.
    """
    if not runs_dir.exists():
        return []
    return sorted(runs_dir.glob("*/*/.state/retry_later.yaml"))


def _lock_path_for(checkpoint_path: Path) -> Path:
    """Per-checkpoint lock dir preventing double-re-dispatch."""
    return checkpoint_path.with_suffix(".yaml.lock")


def _archive_checkpoint(
    checkpoint_path: Path,
    *,
    archive_name: str,
    reason: str,
) -> Path:
    """Rename checkpoint to an archive variant; returns the archived path."""
    archived = checkpoint_path.parent / archive_name
    checkpoint_path.rename(archived)
    # Drop a tombstone so the next daemon scan doesn't re-pick-up.
    reason_path = checkpoint_path.parent / f"{archive_name}.reason"
    reason_path.write_text(reason)
    reason_path.chmod(0o600)
    return archived


def _re_dispatch(
    checkpoint: RetryLaterCheckpoint,
    *,
    env_snapshot: dict[str, str] | None,
    dry_run: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Shell out ``metaproc run-process --from <step_id>``.

    ``runner`` is an injection seam for tests; defaults to
    ``subprocess.run``. Production swaps it only to intercept for
    logging.
    """
    argv = list(checkpoint.dispatch_argv) or [
        "metaproc",
        "run-process",
        "--from",
        checkpoint.from_step_arg or checkpoint.step_id,
    ]
    run_fn: Any = runner if runner is not None else subprocess.run
    if dry_run:
        log.info("resume-daemon: dry-run, would invoke: %s", argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    env = dict(os.environ)
    if env_snapshot:
        env.update(env_snapshot)
    # capture_output so the daemon's log shows what the re-dispatch
    # printed without losing the child's stdout to the console.
    return run_fn(argv, check=False, env=env, capture_output=True, text=True)


@app.command("resume-daemon")
def resume_daemon_cmd(
    runs_dir: Path = typer.Option(
        Path.cwd() / "runs",
        "--runs-dir",
        help="Base directory holding run artifacts (scans for retry_later.yaml).",
    ),
    poll_interval_s: int = typer.Option(
        60, "--poll-interval-s", help="Seconds between checkpoint scans."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scan only — don't spawn re-dispatches."),
    once: bool = typer.Option(
        False, "--once", help="Scan a single time and exit (for cron / tests)."
    ),
    max_iters: int | None = typer.Option(
        None, "--max-iters", help="Stop after this many scans; default is forever."
    ),
) -> None:
    """Poll for retry_later.yaml checkpoints and re-dispatch when ready.

    One daemon per operator (per OQ19). Deliberately small: reads
    files, compares timestamps, shells out. No LLM in the loop
    (G15). Recovery for overnight deadline runs doesn't need
    judgment — it needs a process that runs.
    """
    out = get_output()
    out.progress(
        f"resume-daemon: runs_dir={runs_dir} poll={poll_interval_s}s dry_run={dry_run} once={once}"
    )
    iters = 0
    while True:
        iters += 1
        scanned = 0
        spawned = 0
        archived = 0
        for checkpoint_path in _find_pending_checkpoints(runs_dir):
            scanned += 1
            # Non-blocking idempotency so two daemon instances cannot both
            # re-dispatch the same checkpoint.
            lock_path = _lock_path_for(checkpoint_path)
            try:
                lock_lease = acquire_mkdir_lock(
                    lock_path,
                    timeout=0.0,
                    stale_after=None,
                )
            except MkdirLockTimeoutError:
                log.debug(
                    "resume-daemon: skipping %s (lock held by peer)",
                    checkpoint_path,
                )
                continue
            try:
                try:
                    checkpoint = read_checkpoint(checkpoint_path)
                except (OSError, ValueError) as exc:
                    log.warning(
                        "resume-daemon: cannot read %s: %s",
                        checkpoint_path,
                        exc,
                    )
                    continue

                if (
                    checkpoint.cooling_until_ts is not None
                    and time.time() < checkpoint.cooling_until_ts
                ):
                    log.debug(
                        "resume-daemon: %s not yet ready (ts=%s, now=%s)",
                        checkpoint_path,
                        checkpoint.cooling_until_ts,
                        int(time.time()),
                    )
                    continue

                if checkpoint.retries_attempted >= checkpoint.max_retries:
                    # Out of retries — archive as exhausted so operators
                    # can see what happened without re-scanning every cycle.
                    _archive_checkpoint(
                        checkpoint_path,
                        archive_name="retry_later.exhausted.yaml",
                        reason=(
                            f"retries_attempted={checkpoint.retries_attempted} "
                            f">= max_retries={checkpoint.max_retries}"
                        ),
                    )
                    archived += 1
                    continue

                # Load env snapshot if one exists alongside the checkpoint.
                env_snapshot: dict[str, str] | None = None
                if checkpoint.env_snapshot_ref:
                    env_path = checkpoint_path.parent / checkpoint.env_snapshot_ref
                    if env_path.exists():
                        try:
                            env_snapshot = _json.loads(env_path.read_text())
                        except (OSError, _json.JSONDecodeError) as exc:
                            log.warning(
                                "resume-daemon: cannot read env snapshot %s: %s",
                                env_path,
                                exc,
                            )

                try:
                    result = _re_dispatch(checkpoint, env_snapshot=env_snapshot, dry_run=dry_run)
                    spawned += 1
                except Exception:
                    log.exception("resume-daemon: re-dispatch of %s raised", checkpoint_path)
                    continue

                if result.returncode == 0:
                    _archive_checkpoint(
                        checkpoint_path,
                        archive_name="retry_later.resumed.yaml",
                        reason=f"re-dispatch succeeded at {int(time.time())}",
                    )
                    archived += 1
                    log.info("resume-daemon: re-dispatched %s (ok)", checkpoint_path)
                elif result.returncode == RETRY_LATER_EXIT_CODE:
                    # Still waiting — leave the checkpoint in place, bump
                    # retries_attempted. Next scan will re-evaluate.
                    log.info("resume-daemon: %s re-deferred (exit 78)", checkpoint_path)
                else:
                    # Re-dispatch failed; don't archive, don't loop
                    # forever. Log and leave for the operator.
                    log.warning(
                        "resume-daemon: re-dispatch of %s failed exit=%s stderr=%s",
                        checkpoint_path,
                        result.returncode,
                        (result.stderr or "")[:200],
                    )
            finally:
                try:
                    lock_lease.release()
                except OSError:
                    log.warning(
                        "resume-daemon: failed to release lock %s; remove the lock directory "
                        "after confirming no re-dispatch process is active",
                        lock_path,
                        exc_info=True,
                    )

        out.progress(
            f"resume-daemon iter={iters}: scanned={scanned} spawned={spawned} archived={archived}"
        )
        if once:
            return
        if max_iters is not None and iters >= max_iters:
            return
        try:
            time.sleep(poll_interval_s)
        except KeyboardInterrupt:
            out.progress("resume-daemon: SIGINT; exiting")
            sys.exit(0)
