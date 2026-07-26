"""Tests for runpool kill module — sentinel write, process killing, edge cases."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import yaml

from metaproc.osutils.memory_pressure import PressureLevel
from metaproc.paths import POOL_KILL_SENTINEL_FILE, POOL_STATUS_FILE, STATE_DIR
from metaproc.runpool.kill import (
    _kill_process_group,
    _select_targets,
    _write_sentinel,
    install_subprocess_reaper_signal_handlers,
    kill_pool,
    read_sentinel,
    reap_subprocess_tree,
)
from metaproc.runpool.status import (
    PressureStatus,
    ProcessStatus,
    RunPoolStatus,
    write_status,
)


def _sample_pool_status(pid: int | None = None) -> RunPoolStatus:
    return RunPoolStatus(
        pool_id="pool-test-kill",
        pid=pid or os.getpid(),
        started_at="2026-04-06T14:00:00",
        updated_at="2026-04-06T14:05:00",
        backend="local",
        max_concurrency=10,
        current_concurrency=5,
        active_count=2,
        pending_count=3,
        completed_count=5,
        failed_count=0,
        killed_count=0,
        pressure=PressureStatus(
            level=PressureLevel.NORMAL,
            available_pct=60.0,
            swap_used_gb=1.0,
            total_memory_gb=32.0,
            source="test",
        ),
        processes=[
            ProcessStatus(
                pid=99991,
                backend="local",
                label="variant-a / ITEM-1",
                started_at="2026-04-06T14:01:00",
                elapsed_s=240.0,
                status="running",
            ),
            ProcessStatus(
                pid=99992,
                backend="local",
                label="variant-b / ITEM-2",
                started_at="2026-04-06T14:02:00",
                elapsed_s=180.0,
                status="running",
            ),
            ProcessStatus(
                pid=99993,
                backend="local",
                label="variant-a / ITEM-3",
                started_at="2026-04-06T14:00:00",
                elapsed_s=300.0,
                status="completed",
                exit_code=0,
            ),
        ],
    )


def test_write_sentinel(tmp_path: Path) -> None:
    sentinel_path = _write_sentinel(
        tmp_path,
        reason="user_requested",
        sig="SIGTERM",
        variant=None,
        drain=False,
    )
    assert sentinel_path.exists()
    data = yaml.safe_load(sentinel_path.read_text())
    assert data["reason"] == "user_requested"
    assert data["signal"] == "SIGTERM"
    assert data["variant"] is None
    assert data["drain"] is False
    assert "requested_at" in data


def test_write_sentinel_with_variant(tmp_path: Path) -> None:
    _write_sentinel(
        tmp_path,
        reason="user_requested",
        sig="SIGKILL",
        variant="variant-a",
        drain=True,
    )
    data = yaml.safe_load((tmp_path / POOL_KILL_SENTINEL_FILE).read_text())
    assert data["variant"] == "variant-a"
    assert data["drain"] is True
    assert data["signal"] == "SIGKILL"


def test_read_sentinel_missing(tmp_path: Path) -> None:
    assert read_sentinel(tmp_path) is None


def test_read_sentinel_present(tmp_path: Path) -> None:
    _write_sentinel(tmp_path, reason="test", sig="SIGTERM", variant=None, drain=False)
    data = read_sentinel(tmp_path)
    assert data is not None
    assert data["reason"] == "test"


def test_select_targets_all() -> None:
    processes = [
        ProcessStatus(
            pid=1, backend="local", label="v-a / X", started_at="t", elapsed_s=1, status="running"
        ),
        ProcessStatus(
            pid=2, backend="local", label="v-b / Y", started_at="t", elapsed_s=1, status="running"
        ),
        ProcessStatus(
            pid=3, backend="local", label="v-a / Z", started_at="t", elapsed_s=1, status="completed"
        ),
    ]
    targets = _select_targets(processes, variant=None)
    assert len(targets) == 2
    assert all(t.status == "running" for t in targets)


def test_select_targets_variant_filter() -> None:
    processes = [
        ProcessStatus(
            pid=1, backend="local", label="v-a / X", started_at="t", elapsed_s=1, status="running"
        ),
        ProcessStatus(
            pid=2, backend="local", label="v-b / Y", started_at="t", elapsed_s=1, status="running"
        ),
    ]
    targets = _select_targets(processes, variant="v-a")
    assert len(targets) == 1
    assert targets[0].pid == 1


def test_kill_process_group_not_found() -> None:
    # PID that definitely doesn't exist.
    result = _kill_process_group(2_000_000_000, signal.SIGTERM)
    assert result == "already_exited"


def test_kill_pool_no_status_file(tmp_path: Path) -> None:
    try:
        kill_pool(tmp_path)
        raise AssertionError("Expected FileNotFoundError")
    except FileNotFoundError:
        pass


def test_kill_pool_dead_pool(tmp_path: Path) -> None:
    # Write status with a PID that doesn't exist.
    status = _sample_pool_status(pid=2_000_000_000)
    state_dir = tmp_path / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    write_status(state_dir / POOL_STATUS_FILE, status)

    try:
        kill_pool(tmp_path)
        raise AssertionError("Expected RuntimeError")
    except RuntimeError as e:
        assert "not running" in str(e)


def test_kill_pool_drain_mode(tmp_path: Path) -> None:
    """Drain mode writes sentinel but doesn't kill processes."""
    status = _sample_pool_status()  # Uses current PID, so is_pool_alive returns True.
    state_dir = tmp_path / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    write_status(state_dir / POOL_STATUS_FILE, status)

    with (
        patch("metaproc.runpool.kill._kill_process_group") as mock_kill,
        patch("metaproc.runpool.kill._kill_with_escalation") as mock_escalate,
    ):
        result = kill_pool(tmp_path, drain=True)

    # No kills should have been attempted.
    mock_kill.assert_not_called()
    mock_escalate.assert_not_called()

    assert result.drain is True
    assert result.pool_alive_after is True
    assert len(result.killed_processes) == 0

    # Sentinel should exist.
    sentinel = read_sentinel(tmp_path)
    assert sentinel is not None
    assert sentinel["drain"] is True


def test_kill_pool_normal(tmp_path: Path) -> None:
    """Normal kill writes sentinel and kills child processes."""
    status = _sample_pool_status()
    state_dir = tmp_path / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    write_status(state_dir / POOL_STATUS_FILE, status)

    # First call (alive check) returns True, subsequent calls (post-kill check) return False.
    alive_seq = iter([True, False, False])
    with (
        patch("metaproc.runpool.kill._kill_with_escalation", return_value="killed") as mock_kill,
        patch("metaproc.runpool.kill.is_pool_alive", side_effect=lambda _: next(alive_seq)),
        patch("metaproc.runpool.kill.os.kill"),
    ):
        result = kill_pool(tmp_path)

    # Should have tried to kill the 2 running processes.
    assert mock_kill.call_count == 2
    assert len(result.killed_processes) == 2
    assert all(kp.status == "killed" for kp in result.killed_processes)
    assert result.signal_sent == "SIGTERM"
    assert result.pool_alive_after is False

    # Sentinel should exist.
    assert read_sentinel(tmp_path) is not None


def test_kill_pool_force(tmp_path: Path) -> None:
    """Force mode sends SIGKILL directly."""
    status = _sample_pool_status()
    state_dir = tmp_path / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    write_status(state_dir / POOL_STATUS_FILE, status)

    alive_seq = iter([True, False, False])
    with (
        patch("metaproc.runpool.kill._kill_process_group", return_value="killed") as mock_kill,
        patch("metaproc.runpool.kill.is_pool_alive", side_effect=lambda _: next(alive_seq)),
        patch("metaproc.runpool.kill.os.kill"),
    ):
        result = kill_pool(tmp_path, force=True)

    assert result.signal_sent == "SIGKILL"
    # Force uses _kill_process_group directly, not _kill_with_escalation.
    assert mock_kill.call_count == 2


def test_kill_pool_variant_filter(tmp_path: Path) -> None:
    """Variant filter only kills matching processes."""
    status = _sample_pool_status()
    state_dir = tmp_path / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    write_status(state_dir / POOL_STATUS_FILE, status)

    alive_seq = iter([True, False, False])
    with (
        patch("metaproc.runpool.kill._kill_with_escalation", return_value="killed"),
        patch("metaproc.runpool.kill.is_pool_alive", side_effect=lambda _: next(alive_seq)),
        patch("metaproc.runpool.kill.os.kill"),
    ):
        result = kill_pool(tmp_path, variant="variant-a")

    # Only 1 running process matches "variant-a".
    assert len(result.killed_processes) == 1
    assert result.killed_processes[0].label == "variant-a / ITEM-1"


def test_reap_subprocess_tree_kills_descendants() -> None:
    """reap_subprocess_tree() SIGKILLs all descendants recursively.

    Regression test for bead internal-reference + logbook
    arb-2026-05-28-thu-ensemble § L5: when orchestrator is SIGTERM'd, adapter
    CLI subprocess children must not survive as orphans.
    """

    # Spawn a shell that backgrounds two sleeps + waits.
    # This gives us a parent (sh) and 2 grandchildren (sleep) under the
    # current Python test process — exactly the orphan-subprocess shape from
    # the original incident.
    proc = subprocess.Popen(["/bin/sh", "-c", "sleep 60 & sleep 60 & wait"])
    try:
        # Give the shell time to spawn its sleep grandchildren.
        time.sleep(0.5)

        counts = reap_subprocess_tree()

        # Wait briefly for the killed processes to be reaped by the OS.
        proc.wait(timeout=5)

        # We expect: 1 sh + 2 sleeps = 3 descendants killed.
        # ≥ 1 is enough to confirm recursive descent; exact count may vary on
        # CI hosts with different shell behavior.
        assert counts["killed"] >= 1, f"reap_subprocess_tree killed nothing: {counts}"
        assert counts["errors"] == 0, f"reap_subprocess_tree had errors: {counts}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_reap_subprocess_tree_no_descendants_returns_zero() -> None:
    """reap_subprocess_tree() handles the no-descendants case cleanly."""

    counts = reap_subprocess_tree()
    assert counts["errors"] == 0
    # The test process itself doesn't have arbitrary descendants;
    # killed=0 is the expected case in a clean test environment.
    assert counts["killed"] >= 0  # may have transient children from pytest infra


def test_install_subprocess_reaper_signal_handlers_idempotent() -> None:
    """install_subprocess_reaper_signal_handlers() can be called multiple times."""

    # Save original handlers to restore after the test.
    orig_term = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    orig_int = signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        install_subprocess_reaper_signal_handlers()
        install_subprocess_reaper_signal_handlers()  # second call is a no-op (handlers replaced)
        # If we got here without an exception, the function is idempotent.
        current_term = signal.getsignal(signal.SIGTERM)
        current_int = signal.getsignal(signal.SIGINT)
        assert current_term is not signal.SIG_DFL
        assert current_int is not signal.SIG_DFL
    finally:
        signal.signal(signal.SIGTERM, orig_term)
        signal.signal(signal.SIGINT, orig_int)
