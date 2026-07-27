"""Integration test: concurrent sleep processes with concurrency control."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from metaproc.paths import RUNPOOL_EVENTS_FILE
from metaproc.runpool.backend import PreparedLaunch
from metaproc.runpool.pool import ProcessConfig, RunPool, RunPoolConfig
from metaproc.runpool.status import read_status


class TestRunPoolIntegration:
    def test_concurrent_processes_respect_max_concurrency(self, tmp_path: Path):
        """Run N sleep processes with low max_concurrency.

        Verify:
        - Only max_concurrency processes run at a time.
        - Status file reflects live state.
        - Event log captures all lifecycle events.
        """

        async def _run():
            config = RunPoolConfig(
                max_concurrency=2,
                min_concurrency=1,
                monitor_interval_s=0.3,
                pressure_check_interval_s=600.0,  # Don't interfere.
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
            pool = RunPool(config)

            n_processes = 6
            process_configs = [
                ProcessConfig(
                    launch=PreparedLaunch(command=("sleep", "0.3")),
                    label=f"sleep-{i}",
                )
                for i in range(n_processes)
            ]

            # Track max concurrent.
            max_seen = 0

            async def monitor_concurrency():
                nonlocal max_seen
                while True:
                    count = pool.active_count
                    max_seen = max(max_seen, count)
                    await asyncio.sleep(0.05)

            monitor = asyncio.create_task(monitor_concurrency())

            # Submit all at once.
            results = await pool.submit_batch(process_configs)
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor

            # All should have completed successfully.
            assert len(results) == n_processes
            assert all(r.exit_code == 0 for r in results)

            # Concurrency should never have exceeded 2.
            assert max_seen <= 2

            # Status file should exist.
            status_file = tmp_path / "state" / "runpool-status.yaml"
            assert status_file.exists()
            status = read_status(status_file)
            assert status.completed_count == n_processes

            # Event log should have all lifecycle events.
            events_file = tmp_path / "logs" / RUNPOOL_EVENTS_FILE
            assert events_file.exists()
            events = [json.loads(line) for line in events_file.read_text().strip().split("\n")]
            event_types = [e["event"] for e in events]
            # Should have pool_start, N process_start, N process_exit.
            assert event_types[0] == "pool_start"
            assert event_types.count("process_start") == n_processes
            assert event_types.count("process_exit") == n_processes

            await pool.shutdown()

            # After shutdown, event log should also have pool_shutdown.
            events = [json.loads(line) for line in events_file.read_text().strip().split("\n")]
            assert events[-1]["event"] == "pool_shutdown"
            assert events[-1]["completed"] == n_processes

        asyncio.run(_run())

    def test_mixed_success_and_failure(self, tmp_path: Path):
        """Run a mix of succeeding and failing processes."""

        async def _run():
            config = RunPoolConfig(
                max_concurrency=3,
                monitor_interval_s=0.3,
                pressure_check_interval_s=600.0,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
            pool = RunPool(config)

            process_configs = [
                ProcessConfig(
                    launch=PreparedLaunch(command=("true",)),
                    label="success",
                ),
                ProcessConfig(
                    launch=PreparedLaunch(command=("false",)),
                    label="failure",
                ),
                ProcessConfig(
                    launch=PreparedLaunch(command=("echo", "ok")),
                    label="success-2",
                ),
            ]

            results = await pool.submit_batch(process_configs)
            successes = [r for r in results if r.exit_code == 0]
            failures = [r for r in results if r.exit_code != 0 and r.kill_reason is None]
            assert len(successes) == 2
            assert len(failures) == 1
            await pool.shutdown()

            snap = pool.snapshot
            assert snap.completed_count == 2
            assert snap.failed_count == 1

        asyncio.run(_run())
