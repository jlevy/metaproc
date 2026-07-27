"""Integration test: metaproc kill CLI command."""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from unittest.mock import patch

from strif import atomic_output_file
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.io import to_yaml_string
from metaproc.osutils.memory_pressure import PressureLevel
from metaproc.paths import (
    ORCHESTRATOR_LEASE_FILE,
    POOL_KILL_SENTINEL_FILE,
    POOL_STATUS_FILE,
    STATE_DIR,
)
from metaproc.runpool.status import (
    PressureStatus,
    ProcessStatus,
    RunPoolStatus,
    write_status,
)

runner = CliRunner()


def _write_pool_status(run_dir: Path) -> RunPoolStatus:
    status = RunPoolStatus(
        pool_id="pool-integration-test",
        pid=os.getpid(),
        started_at="2026-04-06T14:00:00",
        updated_at="2026-04-06T14:05:00",
        backend="local",
        max_concurrency=10,
        current_concurrency=3,
        active_count=2,
        pending_count=5,
        completed_count=3,
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
                label="model-a / TICKER-1",
                started_at="2026-04-06T14:01:00",
                elapsed_s=240.0,
                status="running",
            ),
            ProcessStatus(
                pid=99992,
                backend="local",
                label="model-a / TICKER-2",
                started_at="2026-04-06T14:02:00",
                elapsed_s=180.0,
                status="running",
            ),
        ],
    )
    state_dir = run_dir / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    write_status(state_dir / POOL_STATUS_FILE, status)
    return status


def test_kill_cli_no_pool(tmp_path: Path) -> None:
    result = runner.invoke(app, ["kill", str(tmp_path)])
    assert result.exit_code == 1
    assert "No pool status file" in result.output


def test_kill_cli_orchestrator_only(tmp_path: Path) -> None:
    """Before a runpool exists, kill targets the local orchestrator lease owner."""
    lease_dir = tmp_path / STATE_DIR
    lease_dir.mkdir(parents=True)
    lease_path = lease_dir / ORCHESTRATOR_LEASE_FILE
    data = {
        "owner_type": "local",
        "owner_host": os.uname().nodename,
        "owner_pid": 424242,
        "owner_token": "tok",
        "started_at": "2026-05-14T23:00:00",
        "last_heartbeat_at": "2026-05-14T23:00:00",
        "command_summary": "run-process test",
    }
    with atomic_output_file(lease_path) as tmp:
        Path(tmp).write_text(to_yaml_string(data))

    def fake_kill(pid: int, sig: signal.Signals | int) -> None:
        assert pid == 424242
        if sig == 0:
            raise ProcessLookupError

    with patch("metaproc.commands.kill.os.kill", side_effect=fake_kill):
        result = runner.invoke(app, ["kill", str(tmp_path), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["scope"] == "orchestrator"
    assert payload["status"] == "terminated"
    assert payload["lease_cleared"] is True
    assert not lease_path.exists()


def test_kill_cli_dead_pool(tmp_path: Path) -> None:
    status = RunPoolStatus(
        pool_id="pool-dead",
        pid=2_000_000_000,
        started_at="2026-04-06T14:00:00",
        updated_at="2026-04-06T14:05:00",
        backend="local",
        max_concurrency=10,
        current_concurrency=0,
        active_count=0,
        pending_count=0,
        completed_count=10,
        failed_count=0,
        killed_count=0,
        pressure=PressureStatus(
            level=PressureLevel.NORMAL,
            available_pct=60.0,
            swap_used_gb=1.0,
            total_memory_gb=32.0,
            source="test",
        ),
    )
    write_status(tmp_path / POOL_STATUS_FILE, status)

    result = runner.invoke(app, ["kill", str(tmp_path)])
    assert result.exit_code == 1
    assert "not running" in result.output


def test_kill_cli_json_output(tmp_path: Path) -> None:
    _write_pool_status(tmp_path)

    alive_seq = iter([True, False, False])
    with (
        patch("metaproc.runpool.kill._kill_with_escalation", return_value="killed"),
        patch("metaproc.runpool.kill.is_pool_alive", side_effect=lambda _: next(alive_seq)),
        patch("metaproc.runpool.kill.os.kill"),
    ):
        result = runner.invoke(app, ["kill", str(tmp_path), "--format", "json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["pool_id"] == "pool-integration-test"
    assert data["signal_sent"] == "SIGTERM"
    assert len(data["killed_processes"]) == 2

    # Sentinel file should have been written.
    assert (tmp_path / STATE_DIR / POOL_KILL_SENTINEL_FILE).exists()


def test_kill_cli_drain_mode(tmp_path: Path) -> None:
    _write_pool_status(tmp_path)

    with (
        patch("metaproc.runpool.kill._kill_with_escalation") as mock_kill,
        patch("metaproc.runpool.kill._kill_process_group") as mock_kill_pg,
    ):
        result = runner.invoke(app, ["kill", str(tmp_path), "--drain"])

    assert result.exit_code == 0
    mock_kill.assert_not_called()
    mock_kill_pg.assert_not_called()
    assert "Drain mode" in result.output
