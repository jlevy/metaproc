"""Tests for RunPool core."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import psutil
import pytest
import yaml

import metaproc.runpool.pool as pool_module
from metaproc.osutils.memory_pressure import MemoryPressure, PressureLevel
from metaproc.paths import POOL_KILL_SENTINEL_FILE
from metaproc.runpool.backend import HealthMetrics, LaunchHandle, PreparedLaunch
from metaproc.runpool.events import EventLogger
from metaproc.runpool.pool import (
    ProcessConfig,
    RunPool,
    RunPoolConfig,
    SystemHealthSample,
    _classify_disk_free,
    _classify_disk_pressure_cause,
    resolve_estimated_process_rss_bytes,
    resolve_initial_memory_budget_fraction,
)
from metaproc.runpool.status import (
    ControllerStatus,
    RunPoolStatus,
    ScaleOverride,
    ScaleState,
    write_scale_override,
    write_scale_state,
)


@pytest.fixture
def pool_config(tmp_path: Path):
    return RunPoolConfig(
        max_concurrency=3,
        min_concurrency=1,
        monitor_interval_s=0.5,
        pressure_check_interval_s=60.0,  # Don't run pressure checks in tests.
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
    )


class TestRunPool:
    def test_disk_free_pressure_classification(self):
        assert _classify_disk_free(20.0) == PressureLevel.NORMAL
        assert _classify_disk_free(10.0) == PressureLevel.ELEVATED
        assert _classify_disk_free(6.0) == PressureLevel.HIGH
        assert _classify_disk_free(4.0) == PressureLevel.CRITICAL

    def test_disk_pressure_cause_separates_swap_from_generic_disk(self):
        pressure = MemoryPressure(
            available_pct=60.0,
            swap_used_gb=40.0,
            total_memory_gb=32.0,
            level=PressureLevel.NORMAL,
            source="test",
        )
        assert (
            _classify_disk_pressure_cause(
                pressure=pressure,
                disk_level=PressureLevel.HIGH,
                swap_level=PressureLevel.ELEVATED,
                active_log_bytes=0,
            )
            == "swap_growth"
        )
        assert (
            _classify_disk_pressure_cause(
                pressure=pressure,
                disk_level=PressureLevel.HIGH,
                swap_level=PressureLevel.NORMAL,
                active_log_bytes=0,
            )
            == "swap_reserve_high"
        )

    def test_bottleneck_names_disk_cause(self, tmp_path: Path):
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=10,
                initial_concurrency=5,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )
        pool._memory_ceiling = 3
        pool._provider_ceiling = 10
        pool._latest_health = SystemHealthSample(
            pressure=MemoryPressure(
                available_pct=70.0,
                swap_used_gb=40.0,
                total_memory_gb=32.0,
                level=PressureLevel.NORMAL,
                source="test",
            ),
            disk_free_gb=4.0,
            disk_total_gb=460.0,
            disk_used_pct=99.1,
            disk_level=PressureLevel.CRITICAL,
            disk_pressure_cause="swap_reserve_high",
            swap_delta_gb_per_min=0.0,
            swap_level=PressureLevel.NORMAL,
        )

        assert pool._classify_bottleneck() == "disk-bound:swap_reserve_high"

    def test_spawn_and_complete(self, pool_config):
        async def _run():
            pool = RunPool(pool_config)
            config = ProcessConfig(
                launch=PreparedLaunch(command=("echo", "hello")),
                label="test-echo",
            )
            future = pool.submit(config)
            result = await future
            assert result.exit_code == 0
            assert result.backend == "local"
            assert result.pid is not None
            assert result.kill_reason is None
            await pool.shutdown()

        asyncio.run(_run())

    def test_spawn_failing_command(self, pool_config):
        async def _run():
            pool = RunPool(pool_config)
            config = ProcessConfig(
                launch=PreparedLaunch(command=("false",)),
                label="test-false",
            )
            future = pool.submit(config)
            result = await future
            assert result.exit_code != 0
            assert result.kill_reason is None
            await pool.shutdown()

        asyncio.run(_run())

    def test_filter_thread_join_does_not_block_the_event_loop(self, tmp_path: Path) -> None:
        ordering: list[str] = []
        filter_thread = MagicMock()

        def blocking_join(timeout: float) -> None:
            del timeout
            time.sleep(0.1)
            ordering.append("join-finished")

        filter_thread.join.side_effect = blocking_join
        filter_thread.is_alive.return_value = False

        class ExitBackend:
            name = "exit"

            async def launch(
                self,
                prepared: PreparedLaunch,
                label: str = "",
            ) -> LaunchHandle:
                del prepared, label
                return LaunchHandle(
                    external_id="exit",
                    backend_name=self.name,
                    _filter_thread=filter_thread,
                )

            async def poll(self, handle: LaunchHandle) -> int | None:
                del handle
                return 0

            async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
                del handle, sig

            async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
                del handle
                return None

            async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
                del handle, lines
                return ""

        async def _run() -> None:
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=1,
                    initial_concurrency=1,
                    pressure_check_interval_s=60,
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                    host_admission_enabled=False,
                ),
                backend=ExitBackend(),
            )

            async def tick() -> None:
                await asyncio.sleep(0.01)
                ordering.append("event-loop-tick")

            await asyncio.gather(
                pool.submit(
                    ProcessConfig(
                        launch=PreparedLaunch(command=("unused",)),
                        label="filtered",
                    )
                ),
                tick(),
            )
            await pool.shutdown()

        asyncio.run(_run())
        assert ordering == ["event-loop-tick", "join-finished"]

    def test_backend_poll_failure_releases_lane_accounting(self, tmp_path: Path) -> None:
        class PollFailureBackend:
            name = "poll-failure"

            def __init__(self) -> None:
                self.kill_calls = 0

            async def launch(
                self,
                prepared: PreparedLaunch,
                label: str = "",
            ) -> LaunchHandle:
                del prepared, label
                return LaunchHandle(external_id="failed", backend_name=self.name)

            async def poll(self, handle: LaunchHandle) -> int | None:
                del handle
                raise RuntimeError("poll failed")

            async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
                del handle, sig
                self.kill_calls += 1

            async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
                del handle
                return None

            async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
                del handle, lines
                return ""

        async def _run() -> tuple[RunPoolStatus, int]:
            backend = PollFailureBackend()
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=1,
                    initial_concurrency=1,
                    execution_profile="lane",
                    pressure_check_interval_s=60,
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                    host_admission_enabled=False,
                ),
                backend=backend,
            )
            with pytest.raises(RuntimeError, match="poll failed"):
                await pool.submit(
                    ProcessConfig(
                        launch=PreparedLaunch(command=("unused",)),
                        label="broken",
                        lane_id="lane",
                    )
                )
            snapshot = pool.snapshot
            await pool.shutdown()
            return snapshot, backend.kill_calls

        snapshot, kill_calls = asyncio.run(_run())
        lane = next(row for row in snapshot.lanes if row.lane_id == "lane")
        assert lane.active_count == 0
        assert lane.killed_count == 1
        assert kill_calls == 1

    def test_shutdown_collects_and_kills_a_late_backend_launch(self, tmp_path: Path) -> None:
        class LateLaunchBackend:
            name = "late-launch"

            def __init__(self) -> None:
                self.launch_started = asyncio.Event()
                self.allow_launch = asyncio.Event()
                self.kill_calls = 0

            async def launch(
                self,
                prepared: PreparedLaunch,
                label: str = "",
            ) -> LaunchHandle:
                del prepared, label
                self.launch_started.set()
                await self.allow_launch.wait()
                return LaunchHandle(external_id="late", backend_name=self.name)

            async def poll(self, handle: LaunchHandle) -> int | None:
                del handle
                return None

            async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
                del handle, sig
                self.kill_calls += 1

            async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
                del handle
                return None

            async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
                del handle, lines
                return ""

        async def _run() -> tuple[int, bool, int]:
            backend = LateLaunchBackend()
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=1,
                    initial_concurrency=1,
                    min_concurrency=1,
                    pressure_check_interval_s=60,
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                    host_admission_enabled=False,
                ),
                backend=backend,
            )
            result = pool.submit(
                ProcessConfig(
                    launch=PreparedLaunch(command=("unused",)),
                    label="late",
                )
            )
            await backend.launch_started.wait()
            shutdown = asyncio.create_task(pool.shutdown(timeout_s=0))
            await asyncio.sleep(0)
            assert not shutdown.done()
            backend.allow_launch.set()
            await asyncio.wait_for(shutdown, timeout=2)
            return backend.kill_calls, result.cancelled(), pool.snapshot.pending_count

        kill_calls, result_cancelled, pending_count = asyncio.run(_run())
        assert kill_calls == 1
        assert result_cancelled
        assert pending_count == 0

    def test_kill_sentinel_cancels_owned_submissions(self, tmp_path: Path) -> None:
        class BlockingBackend:
            name = "blocking"

            def __init__(self) -> None:
                self.launched = asyncio.Event()
                self.killed = asyncio.Event()

            async def launch(
                self,
                prepared: PreparedLaunch,
                label: str = "",
            ) -> LaunchHandle:
                del prepared, label
                self.launched.set()
                return LaunchHandle(external_id="blocked", backend_name=self.name)

            async def poll(self, handle: LaunchHandle) -> int | None:
                del handle
                return None

            async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
                del handle, sig
                self.killed.set()

            async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
                del handle
                return None

            async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
                del handle, lines
                return ""

        async def _run() -> None:
            backend = BlockingBackend()
            state_dir = tmp_path / "state"
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=1,
                    initial_concurrency=1,
                    monitor_interval_s=0.01,
                    pressure_check_interval_s=0.01,
                    state_dir=state_dir,
                    logs_dir=tmp_path / "logs",
                    host_admission_enabled=False,
                ),
                backend=backend,
            )
            future = pool.submit(
                ProcessConfig(
                    launch=PreparedLaunch(command=("unused",)),
                    label="blocked",
                )
            )
            await backend.launched.wait()
            (state_dir / POOL_KILL_SENTINEL_FILE).write_text(yaml.safe_dump({"reason": "test"}))
            await asyncio.wait_for(backend.killed.wait(), timeout=0.5)
            assert future.cancelled()
            await pool.shutdown()

        asyncio.run(_run())

    def test_shutdown_continues_after_one_backend_kill_error(
        self,
        tmp_path: Path,
    ) -> None:
        class PartlyFailingBackend:
            name = "partly-failing"

            def __init__(self) -> None:
                self.launched = asyncio.Event()
                self.labels: dict[str, str] = {}
                self.killed: set[str] = set()
                self.kill_calls: list[str] = []

            async def launch(
                self,
                prepared: PreparedLaunch,
                label: str = "",
            ) -> LaunchHandle:
                del prepared
                external_id = f"handle-{label}"
                self.labels[external_id] = label
                if len(self.labels) == 2:
                    self.launched.set()
                return LaunchHandle(external_id=external_id, backend_name=self.name)

            async def poll(self, handle: LaunchHandle) -> int | None:
                label = self.labels[handle.external_id or ""]
                return 137 if label in self.killed else None

            async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
                del sig
                label = self.labels[handle.external_id or ""]
                self.kill_calls.append(label)
                self.killed.add(label)
                if label == "first":
                    raise RuntimeError("reported after signalling")

            async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
                del handle
                return None

            async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
                del handle, lines
                return ""

        async def _run() -> tuple[list[str], int]:
            backend = PartlyFailingBackend()
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=2,
                    initial_concurrency=2,
                    monitor_interval_s=0.01,
                    pressure_check_interval_s=60,
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                    host_admission_enabled=False,
                ),
                backend=backend,
            )
            pool.submit(ProcessConfig(launch=PreparedLaunch(command=("unused",)), label="first"))
            pool.submit(ProcessConfig(launch=PreparedLaunch(command=("unused",)), label="second"))
            await backend.launched.wait()
            await pool.shutdown(timeout_s=0)
            return backend.kill_calls, pool.snapshot.active_count

        kill_calls, active_count = asyncio.run(_run())
        assert sorted(kill_calls) == ["first", "second"]
        assert active_count == 0

    def test_shutdown_bounds_wedged_backend_cleanup_and_writes_tail(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class WedgedKillBackend:
            name = "wedged-kill"

            def __init__(self) -> None:
                self.launched = asyncio.Event()
                self.release_kill = asyncio.Event()

            async def launch(
                self,
                prepared: PreparedLaunch,
                label: str = "",
            ) -> LaunchHandle:
                del prepared, label
                self.launched.set()
                return LaunchHandle(external_id="wedged", backend_name=self.name)

            async def poll(self, handle: LaunchHandle) -> int | None:
                del handle
                return None

            async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
                del handle, sig
                await self.release_kill.wait()

            async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
                del handle
                return None

            async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
                del handle, lines
                return ""

        monkeypatch.setattr(pool_module, "_SHUTDOWN_CLEANUP_TIMEOUT_S", 0.05, raising=False)

        async def _run() -> None:
            backend = WedgedKillBackend()
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=1,
                    initial_concurrency=1,
                    monitor_interval_s=0.01,
                    pressure_check_interval_s=60,
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                    host_admission_enabled=False,
                ),
                backend=backend,
            )
            pool.submit(ProcessConfig(launch=PreparedLaunch(command=("unused",)), label="wedged"))
            await backend.launched.wait()
            shutdown = asyncio.create_task(pool.shutdown(timeout_s=0))
            try:
                await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.25)
                events = [
                    json.loads(line)
                    for line in (tmp_path / "logs" / "events.jsonl").read_text().splitlines()
                ]
                assert events[-1]["event"] == "pool_shutdown"
            finally:
                backend.release_kill.set()
                await asyncio.wait_for(shutdown, timeout=1)

        asyncio.run(_run())

    def test_baseexception_still_releases_active_lane_accounting(self, tmp_path: Path) -> None:
        class LifecycleAbort(BaseException):
            pass

        class AbortingBackend:
            name = "aborting"

            async def launch(
                self,
                prepared: PreparedLaunch,
                label: str = "",
            ) -> LaunchHandle:
                del prepared, label
                return LaunchHandle(external_id="abort", backend_name=self.name)

            async def poll(self, handle: LaunchHandle) -> int | None:
                del handle
                raise LifecycleAbort

            async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
                del handle, sig

            async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
                del handle
                return None

            async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
                del handle, lines
                return ""

        async def _run() -> None:
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=1,
                    initial_concurrency=1,
                    execution_profile="lane",
                    monitor_interval_s=0.01,
                    pressure_check_interval_s=60,
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                    host_admission_enabled=False,
                ),
                backend=AbortingBackend(),
            )
            config = ProcessConfig(
                launch=PreparedLaunch(command=("unused",)),
                label="abort",
                lane_id="lane",
            )
            with pytest.raises(LifecycleAbort):
                await pool._launch_and_monitor(config)  # noqa: SLF001
            assert pool.active_count == 0
            lane = next(row for row in pool.snapshot.lanes if row.lane_id == "lane")
            assert lane.active_count == 0
            await pool.shutdown()

        asyncio.run(_run())

    def test_context_manager_keeps_grace_for_normal_errors(self, tmp_path: Path) -> None:
        async def _run() -> None:
            pool = RunPool(
                RunPoolConfig(
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                    host_admission_enabled=False,
                )
            )
            observed: list[float | None] = []

            async def record_shutdown(timeout_s: float | None = None) -> None:
                observed.append(timeout_s)

            pool.shutdown = record_shutdown  # type: ignore[method-assign]
            await pool.__aexit__(RuntimeError, RuntimeError("body failed"), None)
            await pool.__aexit__(asyncio.CancelledError, asyncio.CancelledError(), None)
            assert observed == [30.0, 0.0]

        asyncio.run(_run())

    def test_cancelling_pool_scope_cleans_running_and_queued_siblings(
        self,
        tmp_path: Path,
    ) -> None:
        pid_paths = [tmp_path / "first.pid", tmp_path / "second.pid"]
        script = (
            "import os, sys, time; from pathlib import Path; "
            "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
        )
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=1,
                initial_concurrency=1,
                min_concurrency=1,
                monitor_interval_s=0.01,
                pressure_check_interval_s=60,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
                host_admission_enabled=False,
            )
        )

        async def _run() -> None:
            async def run_scope() -> None:
                try:
                    await asyncio.gather(
                        *[
                            pool.submit(
                                ProcessConfig(
                                    launch=PreparedLaunch(
                                        command=(sys.executable, "-c", script, str(pid_path)),
                                    ),
                                    label=pid_path.stem,
                                )
                            )
                            for pid_path in pid_paths
                        ]
                    )
                finally:
                    await pool.shutdown()

            task = asyncio.create_task(run_scope())
            deadline = time.monotonic() + 5
            while not pid_paths[0].exists():
                assert time.monotonic() < deadline
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)

        asyncio.run(_run())
        assert pool.snapshot.active_count == 0
        assert pool.snapshot.pending_count == 0
        pid = int(pid_paths[0].read_text())
        with pytest.raises(psutil.NoSuchProcess):
            psutil.Process(pid).status()
        assert not pid_paths[1].exists()

    def test_timeout_kill(self, pool_config):
        async def _run():
            pool_config.monitor_interval_s = 0.2
            pool = RunPool(pool_config)
            config = ProcessConfig(
                launch=PreparedLaunch(command=("sleep", "60")),
                timeout_s=0.5,
                label="test-timeout",
            )
            future = pool.submit(config)
            result = await future
            assert result.kill_reason == "timeout"
            assert result.exit_code is None
            await pool.shutdown()

        asyncio.run(_run())

    def test_rss_limit_kill(self, pool_config):
        async def _run():
            pool_config.monitor_interval_s = 0.3
            pool = RunPool(pool_config)
            # Use Python to allocate memory.
            config = ProcessConfig(
                launch=PreparedLaunch(
                    command=(
                        sys.executable,
                        "-c",
                        "import time; x = bytearray(50*1024*1024); time.sleep(30)",
                    ),
                ),
                max_rss_bytes=10 * 1024 * 1024,  # 10 MB — the process will exceed this.
                label="test-rss-kill",
            )
            future = pool.submit(config)
            result = await future
            assert result.kill_reason == "rss_limit"
            await pool.shutdown()

        asyncio.run(_run())

    def test_log_size_limit_kill(self, pool_config, tmp_path: Path):
        async def _run():
            pool_config.monitor_interval_s = 0.3
            log_path = tmp_path / "big.log"
            pool = RunPool(pool_config)
            # Write a lot to stdout (redirected to log).
            config = ProcessConfig(
                launch=PreparedLaunch(
                    command=(
                        sys.executable,
                        "-c",
                        (
                            "import sys; "
                            "[sys.stdout.write('x'*1000+'\\n') for _ in range(10000)];"
                            "import time; time.sleep(30)"
                        ),
                    ),
                    log_path=log_path,
                ),
                max_log_bytes=5000,  # 5 KB — will be exceeded quickly.
                label="test-log-kill",
            )
            future = pool.submit(config)
            result = await future
            assert result.kill_reason == "log_limit"
            await pool.shutdown()

        asyncio.run(_run())

    def test_stall_detection_kill(self, pool_config, tmp_path: Path):
        async def _run():
            pool_config.monitor_interval_s = 0.2
            log_path = tmp_path / "stall.log"
            pool = RunPool(pool_config)
            # Write once then sleep — log stops growing → stall detected.
            config = ProcessConfig(
                launch=PreparedLaunch(
                    command=(
                        sys.executable,
                        "-c",
                        (
                            "import sys; sys.stdout.write('hello\\n'); sys.stdout.flush();"
                            "import time; time.sleep(60)"
                        ),
                    ),
                    log_path=log_path,
                ),
                stall_timeout_s=1.0,  # 1 second stall threshold for test.
                label="test-stall",
            )
            future = pool.submit(config)
            result = await future
            assert result.kill_reason == "stalled"
            await pool.shutdown()

        asyncio.run(_run())

    def test_descendant_limit_kill(self, pool_config):
        async def _run():
            pool_config.monitor_interval_s = 0.3
            pool = RunPool(pool_config)
            # Spawn many child processes.
            config = ProcessConfig(
                launch=PreparedLaunch(
                    command=(
                        sys.executable,
                        "-c",
                        (
                            "import subprocess, time;"
                            "[subprocess.Popen(['sleep','30']) for _ in range(10)];"
                            "time.sleep(30)"
                        ),
                    ),
                ),
                max_descendants=3,
                label="test-descendants-kill",
            )
            future = pool.submit(config)
            result = await future
            assert result.kill_reason == "descendants_limit"
            await pool.shutdown()

        asyncio.run(_run())

    def test_multiple_processes(self, pool_config):
        async def _run():
            pool = RunPool(pool_config)
            configs = [
                ProcessConfig(
                    launch=PreparedLaunch(command=("echo", f"item-{i}")),
                    label=f"echo-{i}",
                )
                for i in range(5)
            ]
            results = await pool.submit_batch(configs)
            assert len(results) == 5
            assert all(r.exit_code == 0 for r in results)
            await pool.shutdown()

        asyncio.run(_run())

    def test_concurrency_limit(self, pool_config):
        async def _run():
            pool_config.max_concurrency = 2
            pool = RunPool(pool_config)
            configs = [
                ProcessConfig(
                    launch=PreparedLaunch(command=("sleep", "0.3")),
                    label=f"sleep-{i}",
                )
                for i in range(4)
            ]
            futures = pool.submit_many(configs)
            # At most 2 should be active at any time.
            await asyncio.sleep(0.1)
            assert pool.active_count <= 2
            for f in futures:
                await f
            await pool.shutdown()

        asyncio.run(_run())

    def test_status_file_written(self, pool_config, tmp_path: Path):
        async def _run():
            pool = RunPool(pool_config)
            config = ProcessConfig(
                launch=PreparedLaunch(command=("sleep", "0.5")),
                label="status-test",
            )
            future = pool.submit(config)
            await asyncio.sleep(0.2)
            # Status file should exist.
            state_dir = tmp_path / "state"
            assert state_dir.exists()
            status_file = state_dir / "runpool-status.yaml"
            assert status_file.exists()
            await future
            await pool.shutdown()

        asyncio.run(_run())

    def test_event_log_written(self, pool_config, tmp_path: Path):
        async def _run():
            pool = RunPool(pool_config)
            config = ProcessConfig(
                launch=PreparedLaunch(command=("echo", "hi")),
                label="event-test",
            )
            future = pool.submit(config)
            await future
            await pool.shutdown()
            # Event log should have pool_start, process_start, process_exit, pool_shutdown.
            logs_dir = tmp_path / "logs"
            events_file = logs_dir / "events.jsonl"
            assert events_file.exists()

            events = [json.loads(line) for line in events_file.read_text().strip().split("\n")]
            event_types = [e["event"] for e in events]
            assert "pool_start" in event_types
            assert "process_start" in event_types
            assert "process_exit" in event_types
            assert "pool_shutdown" in event_types

        asyncio.run(_run())

    def test_health_log_written(self, tmp_path: Path):
        async def _run():
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=1,
                    initial_concurrency=1,
                    min_concurrency=1,
                    monitor_interval_s=0.05,
                    pressure_check_interval_s=0.05,
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                )
            )
            config = ProcessConfig(
                launch=PreparedLaunch(
                    command=(
                        sys.executable,
                        "-c",
                        "import time; time.sleep(0.2)",
                    )
                ),
                label="health-test",
            )
            result = await pool.submit(config)
            await pool.shutdown()
            assert result.exit_code == 0

        asyncio.run(_run())

        health_file = tmp_path / "logs" / "health.jsonl"
        assert health_file.exists()
        samples = [json.loads(line) for line in health_file.read_text().splitlines()]
        assert len(samples) >= 2
        sample = samples[-1]
        assert sample["event"] == "health_sample"
        assert sample["total_memory_gb"] > 0
        assert sample["disk_free_gb"] >= 0
        assert sample["disk_pressure_cause"] in {
            "none",
            "swap_growth",
            "swap_reserve_high",
            "active_logs",
            "low_disk_unknown",
        }
        assert "active_rss_bytes" in sample

    def test_pool_start_logs_resolved_initial_concurrency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "metaproc.runpool.pool.estimate_initial_concurrency",
            lambda *_args, **_kwargs: 2,
        )

        async def _run():
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=5,
                    initial_concurrency=0,
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                )
            )
            config = ProcessConfig(
                launch=PreparedLaunch(command=("echo", "hi")),
                label="event-test",
            )
            await pool.submit(config)
            await pool.shutdown()

        asyncio.run(_run())

        events_file = tmp_path / "logs" / "events.jsonl"
        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        pool_start = next(event for event in events if event["event"] == "pool_start")
        assert pool_start["initial_concurrency"] == 2
        assert pool_start["concurrency_plan"]["initial_concurrency_estimate"] == 2

    def test_event_logger_records_pressure_resource_fields(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        with EventLogger(events_file) as logger:
            logger.pressure_check(
                "normal",
                72.4,
                swap_used_gb=1.25,
                total_memory_gb=32.0,
                swap_delta_gb_per_min=0.4,
                swap_level="elevated",
                disk_free_gb=9.5,
                disk_total_gb=460.0,
                disk_used_pct=97.9,
                disk_level="elevated",
                disk_pressure_cause="swap_growth",
                source="macos-memorystatus",
                current_concurrency=5,
                active_count=4,
                pending_count=9,
                memory_ceiling=6,
                provider_ceiling=15,
                operator_cap=15,
                effective_target=6,
                bottleneck="memory-bound",
                active_rss_bytes=8_589_934_592,
                active_peak_rss_bytes=10_737_418_240,
                active_log_bytes=100,
            )
            logger.concurrency_adjust(
                5,
                6,
                "pressure_normal",
                memory_ceiling=6,
                provider_ceiling=15,
                operator_cap=15,
                effective_target=6,
                bottleneck="memory-bound",
            )

        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        pressure = events[0]
        assert pressure["event"] == "pressure_check"
        assert pressure["active_count"] == 4
        assert pressure["current_concurrency"] == 5
        assert pressure["active_rss_bytes"] == 8_589_934_592
        assert pressure["total_memory_gb"] == 32.0
        assert pressure["swap_delta_gb_per_min"] == 0.4
        assert pressure["disk_free_gb"] == 9.5
        assert pressure["disk_level"] == "elevated"
        assert pressure["disk_pressure_cause"] == "swap_growth"
        assert pressure["active_log_bytes"] == 100
        assert pressure["bottleneck"] == "memory-bound"

        adjustment = events[1]
        assert adjustment["event"] == "concurrency_adjust"
        assert adjustment["effective_target"] == 6
        assert adjustment["memory_ceiling"] == 6

    def test_graceful_shutdown(self, pool_config):
        async def _run():
            pool = RunPool(pool_config)
            config = ProcessConfig(
                launch=PreparedLaunch(command=("sleep", "60")),
                label="shutdown-test",
            )
            pool.submit(config)
            await asyncio.sleep(0.3)
            assert pool.active_count == 1
            await pool.shutdown(timeout_s=2.0)
            assert pool.active_count == 0

        asyncio.run(_run())

    def test_prepare_launch_callback(self, pool_config):
        async def _run():
            pool = RunPool(pool_config)
            call_count = 0

            def make_launch():
                nonlocal call_count
                call_count += 1
                return PreparedLaunch(command=("echo", "prepared"))

            config = ProcessConfig(
                prepare_launch=make_launch,
                label="prepare-test",
            )
            future = pool.submit(config)
            result = await future
            assert result.exit_code == 0
            assert call_count == 1
            await pool.shutdown()

        asyncio.run(_run())

    def test_snapshot(self, pool_config):
        async def _run():
            pool = RunPool(pool_config)
            config = ProcessConfig(
                launch=PreparedLaunch(command=("sleep", "1")),
                label="snapshot-test",
            )
            pool.submit(config)
            await asyncio.sleep(0.2)
            snap = pool.snapshot
            assert snap.active_count == 1
            assert snap.pool_id == pool.pool_id
            assert snap.backend == "local"
            await pool.shutdown(timeout_s=2.0)

        asyncio.run(_run())

    def test_external_semaphore_limits_global_concurrency(self, tmp_path: Path):
        """External semaphore limits total concurrency across pools."""

        async def _run():
            # Global semaphore allows 2 total, each pool allows 3.
            ext_sem = asyncio.Semaphore(2)

            config_a = RunPoolConfig(
                max_concurrency=3,
                min_concurrency=1,
                monitor_interval_s=0.5,
                pressure_check_interval_s=60.0,
                state_dir=tmp_path / "a" / "state",
                logs_dir=tmp_path / "a" / "logs",
                external_semaphore=ext_sem,
            )
            config_b = RunPoolConfig(
                max_concurrency=3,
                min_concurrency=1,
                monitor_interval_s=0.5,
                pressure_check_interval_s=60.0,
                state_dir=tmp_path / "b" / "state",
                logs_dir=tmp_path / "b" / "logs",
                external_semaphore=ext_sem,
            )

            pool_a = RunPool(config_a)
            pool_b = RunPool(config_b)

            # Submit 2 slow items to pool_a and 2 to pool_b.
            futures_a = [
                pool_a.submit(
                    ProcessConfig(
                        launch=PreparedLaunch(command=("sleep", "0.5")),
                        label=f"a-{i}",
                    )
                )
                for i in range(2)
            ]
            futures_b = [
                pool_b.submit(
                    ProcessConfig(
                        launch=PreparedLaunch(command=("sleep", "0.5")),
                        label=f"b-{i}",
                    )
                )
                for i in range(2)
            ]

            await asyncio.sleep(0.2)
            # At most 2 should be active across both pools (external semaphore limit).
            total_active = pool_a.active_count + pool_b.active_count
            assert total_active <= 2

            for f in futures_a + futures_b:
                await f
            await pool_a.shutdown()
            await pool_b.shutdown()

        asyncio.run(_run())

    def test_host_admission_limits_concurrency_across_pools(self, tmp_path: Path):
        async def _run():
            config_a = RunPoolConfig(
                max_concurrency=2,
                initial_concurrency=2,
                min_concurrency=1,
                monitor_interval_s=0.05,
                pressure_check_interval_s=60.0,
                state_dir=tmp_path / "a" / "state",
                logs_dir=tmp_path / "a" / "logs",
                host_admission_enabled=True,
                host_admission_dir=tmp_path / "host-slots",
                host_admission_limit=1,
            )
            config_b = RunPoolConfig(
                max_concurrency=2,
                initial_concurrency=2,
                min_concurrency=1,
                monitor_interval_s=0.05,
                pressure_check_interval_s=60.0,
                state_dir=tmp_path / "b" / "state",
                logs_dir=tmp_path / "b" / "logs",
                host_admission_enabled=True,
                host_admission_dir=tmp_path / "host-slots",
                host_admission_limit=1,
            )
            pool_a = RunPool(config_a)
            pool_b = RunPool(config_b)

            futures = [
                pool_a.submit(
                    ProcessConfig(
                        launch=PreparedLaunch(command=("sleep", "0.3")),
                        label="a",
                    )
                ),
                pool_b.submit(
                    ProcessConfig(
                        launch=PreparedLaunch(command=("sleep", "0.3")),
                        label="b",
                    )
                ),
            ]
            await asyncio.sleep(0.1)
            assert pool_a.active_count + pool_b.active_count <= 1
            for future in futures:
                result = await future
                assert result.exit_code == 0
            await pool_a.shutdown()
            await pool_b.shutdown()

        asyncio.run(_run())

        assert not list((tmp_path / "host-slots" / "local-agents").glob("slot-*"))

    def test_host_admission_events_are_logged(self, tmp_path: Path):
        async def _run():
            pool = RunPool(
                RunPoolConfig(
                    max_concurrency=1,
                    initial_concurrency=1,
                    min_concurrency=1,
                    monitor_interval_s=0.05,
                    pressure_check_interval_s=60.0,
                    state_dir=tmp_path / "state",
                    logs_dir=tmp_path / "logs",
                    host_admission_enabled=True,
                    host_admission_dir=tmp_path / "host-slots",
                    host_admission_limit=1,
                )
            )
            future = pool.submit(
                ProcessConfig(
                    launch=PreparedLaunch(command=("echo", "hi")),
                    label="event-host-slot",
                )
            )
            result = await future
            assert result.exit_code == 0
            await pool.shutdown()

        asyncio.run(_run())

        events_file = tmp_path / "logs" / "events.jsonl"
        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        event_types = [event["event"] for event in events]
        assert "host_slot_acquired" in event_types
        assert "host_slot_released" in event_types

    def test_pressure_adjusts_live_pool_capacity(self, tmp_path: Path):
        """RunPool updates its semaphore capacity when pressure changes."""
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=10,
                min_concurrency=1,
                hysteresis_checks=3,
                pressure_check_interval_s=60.0,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        assert pool.current_max_concurrency == 10

        pool._adjust_concurrency(PressureLevel.NORMAL)
        pool._adjust_concurrency(PressureLevel.NORMAL)
        assert pool.current_max_concurrency == 10

        pool._adjust_concurrency(PressureLevel.NORMAL)
        assert pool.current_max_concurrency == 11

        pool._adjust_concurrency(PressureLevel.HIGH)
        assert pool.current_max_concurrency == 8

        pool._adjust_concurrency(PressureLevel.CRITICAL)
        assert pool.current_max_concurrency == 4

    def test_runtime_memory_hints_resolve_from_adapter_config(self):
        assert (
            resolve_estimated_process_rss_bytes({"estimated_process_rss_mb": "4096"})
            == 4096 * 1024 * 1024
        )
        assert resolve_estimated_process_rss_bytes({"estimated_process_rss_bytes": "1234"}) == 1234
        assert (
            resolve_initial_memory_budget_fraction({"initial_memory_budget_fraction": "0.25"})
            == 0.25
        )

    def test_auto_initial_concurrency_uses_runtime_memory_hints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict[str, float | int] = {}

        def fake_estimate(
            max_concurrency: int,
            per_process_bytes: int,
            *,
            budget_fraction: float,
            pressure: MemoryPressure | None = None,
        ) -> int:
            captured["max_concurrency"] = max_concurrency
            captured["per_process_bytes"] = per_process_bytes
            captured["budget_fraction"] = budget_fraction
            captured["pressure_total_memory_gb"] = pressure.total_memory_gb if pressure else 0
            return 3

        monkeypatch.setattr("metaproc.runpool.pool.estimate_initial_concurrency", fake_estimate)
        monkeypatch.setattr(
            "metaproc.runpool.pool.validate_supported_platform",
            lambda: MemoryPressure(
                available_pct=75.0,
                swap_used_gb=0.0,
                total_memory_gb=32.0,
                level=PressureLevel.NORMAL,
                source="test",
            ),
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=20,
                initial_concurrency=0,
                estimated_process_rss_bytes=4096 * 1024 * 1024,
                initial_memory_budget_fraction=0.25,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        assert pool.current_max_concurrency == 3
        assert captured["max_concurrency"] == 20
        assert captured["per_process_bytes"] == 4096 * 1024 * 1024
        assert captured["budget_fraction"] == 0.25
        assert captured["pressure_total_memory_gb"] == 32.0

    def test_rate_limit_burst_adjusts_live_pool_capacity(self, tmp_path: Path):
        """RunPool cuts capacity when recent rate-limit failures burst.

        Burst floor = max(min_concurrency, max_concurrency // 4) = max(1, 12) = 12.
        50% of 20 = 10, but floored to 12.
        """
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                rate_limit_burst_threshold=3,
                rate_limit_burst_window_s=60.0,
                rate_limit_down_factor=0.5,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        assert pool.current_max_concurrency == 20

        pool.record_failure_class("rate_limited")
        pool.record_failure_class("rate_limited")
        assert pool.current_max_concurrency == 20

        pool.record_failure_class("rate_limited")
        assert pool.current_max_concurrency == 12

    def test_provider_recovery_via_hysteresis(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Provider ceiling recovers after 3 consecutive clean ticks (no cooldown timer)."""
        now = {"value": 1_000.0}

        monkeypatch.setattr(
            "metaproc.runpool.pool.time.monotonic",
            lambda: now["value"],
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                hysteresis_checks=3,
                rate_limit_burst_threshold=1,
                rate_limit_burst_window_s=60.0,
                rate_limit_down_factor=0.5,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        pool.record_failure_class("rate_limited")
        # Burst floor = max(1, 50 // 4) = 12, so 50% of 20 = 10 is floored to 12
        assert pool.current_max_concurrency == 12

        # Rate limit is still in the burst window — provider stays blocked.
        pool._adjust_concurrency(PressureLevel.NORMAL)
        pool._adjust_concurrency(PressureLevel.NORMAL)
        pool._adjust_concurrency(PressureLevel.NORMAL)
        assert pool.current_max_concurrency == 12

        # Advance past the burst window so the deque drains.
        now["value"] += 61.0
        # 3 consecutive clear ticks → provider recovers (+10% of 12 = +1).
        pool._adjust_concurrency(PressureLevel.NORMAL)
        pool._adjust_concurrency(PressureLevel.NORMAL)
        pool._adjust_concurrency(PressureLevel.NORMAL)
        assert pool.current_max_concurrency == 13

    def test_rate_limit_backoff_updates_controller_state(self, tmp_path: Path):
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                rate_limit_burst_threshold=1,
                rate_limit_burst_window_s=60.0,
                rate_limit_down_factor=0.5,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        pool.record_failure_class("rate_limited")
        status = pool.snapshot

        # Burst floor = max(1, 50 // 4) = 12
        assert status.current_concurrency == 12
        assert status.controller is not None
        assert status.controller.provider_ceiling == 12
        assert status.controller.memory_ceiling == 20
        assert status.controller.bottleneck == "DSQ-bound"

    def test_memory_pressure_reduces_capacity_independently_of_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Memory governor reduces capacity even while provider ceiling is active."""
        now = {"value": 1_000.0}

        monkeypatch.setattr(
            "metaproc.runpool.pool.time.monotonic",
            lambda: now["value"],
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                hysteresis_checks=3,
                rate_limit_burst_threshold=1,
                rate_limit_burst_window_s=60.0,
                rate_limit_down_factor=0.1,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        pool.record_failure_class("rate_limited")
        assert pool.current_max_concurrency == 18

        pool._adjust_concurrency(PressureLevel.HIGH)
        status = pool.snapshot

        assert pool.current_max_concurrency == 15
        assert status.controller is not None
        assert status.controller.memory_ceiling == 15
        assert status.controller.provider_ceiling == 18
        assert status.controller.effective_target == 15

    def test_provider_recovery_independent_of_memory_pressure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Provider governor recovers even when memory pressure is ELEVATED."""
        now = {"value": 1_000.0}

        monkeypatch.setattr(
            "metaproc.runpool.pool.time.monotonic",
            lambda: now["value"],
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                hysteresis_checks=3,
                rate_limit_burst_threshold=1,
                rate_limit_burst_window_s=60.0,
                rate_limit_down_factor=0.5,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        pool.record_failure_class("rate_limited")
        # Burst floor = max(1, 50 // 4) = 12
        assert pool.current_max_concurrency == 12

        # Advance past burst window.
        now["value"] += 61.0

        # Provider recovery should work even under ELEVATED memory pressure.
        pool._adjust_concurrency(PressureLevel.ELEVATED)
        pool._adjust_concurrency(PressureLevel.ELEVATED)
        pool._adjust_concurrency(PressureLevel.ELEVATED)
        # ELEVATED is a HOLD signal (matches PressureLevel docstring) — memory
        # ceiling stays at its current value. Provider ceiling still recovers.
        status = pool.snapshot
        assert status.controller is not None
        assert status.controller.provider_ceiling == 13  # 12 + 10% = 13
        assert status.controller.memory_ceiling == 20  # unchanged at ELEVATED

    def test_burst_floor_prevents_cascading_collapse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Multiple burst cuts cannot push capacity below 25% of max."""
        now = {"value": 1_000.0}

        monkeypatch.setattr(
            "metaproc.runpool.pool.time.monotonic",
            lambda: now["value"],
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=100,
                initial_concurrency=50,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                rate_limit_burst_threshold=1,
                rate_limit_burst_window_s=60.0,
                rate_limit_down_factor=0.5,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        # First burst: 50 * 0.5 = 25, floor = max(1, 100 // 4) = 25
        pool.record_failure_class("rate_limited")
        assert pool.current_max_concurrency == 25

        # Second burst: would compute 25 * 0.5 = 12, but floor is 25
        now["value"] += 1.0
        pool.record_failure_class("rate_limited")
        assert pool.current_max_concurrency == 25

    def test_provider_recovery_allowed_when_retries_draining(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Provider recovers when pending retries are decreasing (draining)."""
        now = {"value": 1_000.0}

        monkeypatch.setattr(
            "metaproc.runpool.pool.time.monotonic",
            lambda: now["value"],
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                hysteresis_checks=3,
                rate_limit_burst_threshold=1,
                rate_limit_burst_window_s=60.0,
                rate_limit_down_factor=0.5,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        pool.record_failure_class("rate_limited")
        assert pool.current_max_concurrency == 12

        # Simulate retries being scheduled then draining.
        pool._pending_retries = 5
        now["value"] += 61.0  # clear burst window

        # Tick 1: retries stable at 5 (first tick sets baseline).
        pool._adjust_concurrency(PressureLevel.NORMAL)
        # Tick 2-4: retries draining (3 → 2 → 1).
        pool._pending_retries = 3
        pool._adjust_concurrency(PressureLevel.NORMAL)
        pool._pending_retries = 2
        pool._adjust_concurrency(PressureLevel.NORMAL)
        pool._pending_retries = 1
        pool._adjust_concurrency(PressureLevel.NORMAL)
        # Provider should have recovered after 3 clean ticks with draining retries.
        assert pool._provider_ceiling == 13  # 12 + 10% = 13

    def test_scale_state_restored_on_init(self, tmp_path: Path):
        """Provider state is restored from scale-state.yaml; memory state is not.

        The provider ceiling describes the run and the account, so it survives a
        resume. The memory ceiling describes the host, which is free to have changed,
        so it always comes from the fresh estimate — here the configured
        ``initial_concurrency`` of 20 rather than the saved 25.
        """

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)

        # Write a scale-state.yaml with reduced provider ceiling.
        write_scale_state(
            state_dir / "scale-state.yaml",
            ScaleState(
                updated_at="2026-04-16T00:00:00Z",
                controller=ControllerStatus(
                    operator_cap=50,
                    effective_target=15,
                    memory_ceiling=25,
                    provider_ceiling=15,
                    bottleneck="DSQ-bound",
                ),
            ),
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=tmp_path / "logs",
            )
        )

        assert pool._memory_ceiling == 20
        assert pool._provider_ceiling == 15
        assert pool.current_max_concurrency == 15

    def test_saved_memory_ceiling_cannot_burst_a_resume(self, tmp_path: Path):
        """A ceiling earned on a quiet host must not be handed to a busy one.

        This is the burst-on-resume shape that the operating runbook attributes two
        host crashes to, reproduced at its smallest: a prior run ramps to 79, the
        machine is now good for 4, and the resume must open at 4. Restoring the saved
        figure would put 79 launches' worth of intent onto a host that cannot hold
        them, and the pool enforces its ceiling faithfully, so nothing downstream
        would catch it.
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)
        write_scale_state(
            state_dir / "scale-state.yaml",
            ScaleState(
                updated_at="2026-04-16T00:00:00Z",
                controller=ControllerStatus(
                    operator_cap=100,
                    effective_target=79,
                    memory_ceiling=79,
                    provider_ceiling=79,
                    bottleneck="none",
                ),
            ),
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=100,
                initial_concurrency=4,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=tmp_path / "logs",
            )
        )

        assert pool._memory_ceiling == 4
        assert pool.current_max_concurrency == 4

    def test_elevated_holds_not_reduces(self, tmp_path: Path):
        """Regression for sustained elevated pressure:
        must not ratchet memory_ceiling down to min_concurrency over time.

        PressureLevel.ELEVATED's docstring says "20-40% — hold current
        concurrency"; the scaler now matches that. Reductions only fire
        at HIGH (10-20%) and CRITICAL (<10%).
        """
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=30,
                initial_concurrency=13,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                hysteresis_checks=3,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )
        # Simulate sustained ELEVATED pressure for many ticks
        # (what happens during two parallel dispatches).
        for _ in range(30):
            pool._adjust_concurrency(PressureLevel.ELEVATED)
        # memory_ceiling holds at the starting value — never ratchets.
        assert pool._memory_ceiling == 13, (
            "Sustained ELEVATED pressure must not reduce memory_ceiling — "
            "ELEVATED is a HOLD signal per the PressureLevel docstring."
        )

    def test_high_still_reduces(self, tmp_path: Path):
        """HIGH (10-20% available) still reduces — that's where memory is
        genuinely tight. Only ELEVATED holds."""
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=30,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )
        starting = pool._memory_ceiling
        pool._adjust_concurrency(PressureLevel.HIGH)
        assert pool._memory_ceiling < starting, "HIGH pressure must still reduce memory_ceiling"

    def test_restored_governor_ceilings_floored_at_fresh_estimate(self, tmp_path: Path):
        """Regression for a prior low-cap run:
        converged ceiling must not pin a relaunch at a higher cap.

        Sequence: launch at `--max-concurrency 3` → scaler converges at
        `memory_ceiling=2` → scale-state.yaml saved → kill → relaunch at
        `--max-concurrency 30` (initial_concurrency=12 fresh estimate) →
        the restored `memory_ceiling=2` must be floored up to 12, not
        kept at 2.

        `provider_ceiling` is also floored when the restored state has no
        recent provider pressure; otherwise a stale DSQ-bound state can pin
        the higher-cap relaunch.
        """

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)

        # Prior run's saved state: converged to memory_ceiling=2 under a
        # low operator cap.
        write_scale_state(
            state_dir / "scale-state.yaml",
            ScaleState(
                updated_at="2026-05-13T19:55:30Z",
                controller=ControllerStatus(
                    operator_cap=3,
                    effective_target=2,
                    memory_ceiling=2,
                    provider_ceiling=3,
                    bottleneck="memory-bound",
                ),
            ),
        )

        # Relaunch at a much higher cap.
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=30,
                initial_concurrency=12,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=tmp_path / "logs",
            )
        )

        # Memory ceiling is floored at the fresh estimate (12), not pinned
        # at the stale 2.
        assert pool._memory_ceiling == 12
        # Provider ceiling is also floored because there are no recent
        # rate-limit signals or pending retries in the restored state.
        assert pool._provider_ceiling == 12

    def test_restored_provider_ceiling_preserved_with_recent_provider_pressure(
        self, tmp_path: Path
    ):
        """Recent rate-limit state remains sticky across a higher-cap resume."""

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)

        write_scale_state(
            state_dir / "scale-state.yaml",
            ScaleState(
                updated_at="2026-05-13T19:55:30Z",
                controller=ControllerStatus(
                    operator_cap=3,
                    effective_target=2,
                    memory_ceiling=2,
                    provider_ceiling=3,
                    bottleneck="DSQ-bound",
                    recent_rate_limits=2,
                    pending_retries=1,
                ),
            ),
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=30,
                initial_concurrency=12,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=tmp_path / "logs",
            )
        )

        assert pool._memory_ceiling == 12
        assert pool._provider_ceiling == 3

    def test_operator_override_via_scale_override_file(self, tmp_path: Path):
        """Operator can set cap via scale-override.yaml."""

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=tmp_path / "logs",
            )
        )

        # Write operator override.
        override_path = state_dir / "scale-override.yaml"
        override_path.write_text(yaml.dump({"operator_cap": 10}))

        pool._read_overrides()
        assert pool._operator_cap == 10
        assert pool._effective_target() == 10

    def test_operator_override_applies_in_adaptive_mode(self, tmp_path: Path):
        """Adaptive pools apply operator cap changes even when ceilings do not change."""

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=20,
                initial_concurrency=5,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=tmp_path / "logs",
            )
        )
        pool._memory_ceiling = 20
        pool._provider_ceiling = 20

        (state_dir / "scale-override.yaml").write_text(yaml.dump({"operator_cap": 8}))
        pool._read_overrides()
        pool._apply_operator_target()
        assert pool.current_max_concurrency == 8

        (state_dir / "scale-override.yaml").write_text(yaml.dump({"operator_cap": 3}))
        pool._read_overrides()
        pool._apply_operator_target()
        assert pool.current_max_concurrency == 3

    def test_run_level_operator_override_clamps_startup_capacity(self, tmp_path: Path):
        """Future step pools inherit a run-level override before first launch."""

        run_dir = tmp_path / "run"
        state_dir = run_dir / ".state" / "steps" / "fanout"
        write_scale_override(
            run_dir / ".state" / "scale-override.yaml",
            ScaleOverride(operator_cap=4),
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=20,
                initial_concurrency=10,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=run_dir / ".logs" / "runpool" / "steps" / "fanout",
            )
        )

        assert pool._operator_cap == 4
        assert pool._effective_target() == 4
        assert pool.current_max_concurrency == 4

    def test_nested_run_level_operator_override_clamps_startup_capacity(self, tmp_path: Path):
        """Nested process pools read the nearest run-level override."""

        nested_run_dir = tmp_path / "run" / "analysis-research"
        state_dir = nested_run_dir / ".state" / "steps" / "fanout"
        write_scale_override(
            nested_run_dir / ".state" / "scale-override.yaml",
            ScaleOverride(operator_cap=2),
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=20,
                initial_concurrency=10,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=nested_run_dir / ".logs" / "runpool" / "steps" / "fanout",
            )
        )

        assert pool._operator_cap == 2
        assert pool.current_max_concurrency == 2

    def test_pool_local_override_takes_precedence_over_run_level(self, tmp_path: Path):
        """Step-specific operator override wins over the run-level default."""

        run_dir = tmp_path / "run"
        state_dir = run_dir / ".state" / "steps" / "fanout"
        write_scale_override(
            run_dir / ".state" / "scale-override.yaml",
            ScaleOverride(operator_cap=6),
        )
        write_scale_override(
            state_dir / "scale-override.yaml",
            ScaleOverride(operator_cap=3, mode="manual"),
        )

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=20,
                initial_concurrency=10,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=run_dir / ".logs" / "runpool" / "steps" / "fanout",
            )
        )

        assert pool._operator_mode_override == "manual"
        assert pool._operator_cap == 3
        assert pool.current_max_concurrency == 3

    def test_operator_manual_mode_freezes_governors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Manual mode in scale-override.yaml skips governor adjustments."""

        now = {"value": 1_000.0}
        monkeypatch.setattr("metaproc.runpool.pool.time.monotonic", lambda: now["value"])

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)

        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=state_dir,
                logs_dir=tmp_path / "logs",
            )
        )

        # Set manual mode with explicit cap.
        override_path = state_dir / "scale-override.yaml"
        override_path.write_text(yaml.dump({"mode": "manual", "operator_cap": 8}))

        pool._read_overrides()
        assert pool._operator_mode_override == "manual"
        assert pool._operator_cap == 8

    def test_bottleneck_both_ceilings_equal(self, tmp_path: Path):
        """When both ceilings are equal and below operator cap, report both."""
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=50,
                initial_concurrency=20,
                min_concurrency=1,
                pressure_check_interval_s=60.0,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )
        pool._memory_ceiling = 15
        pool._provider_ceiling = 15
        assert pool._classify_bottleneck() == "DSQ-bound+memory-bound"

    def test_zero_429s_dual_governor_matches_single_axis(self, tmp_path: Path):
        """Regression: with zero 429s the dual-governor behaves identically to
        pure memory-pressure control.

        The provider governor must remain inert (provider_ceiling stays at
        max_concurrency) so that the effective target is entirely determined by
        memory pressure, exactly as the prior single-axis controller would have
        computed.
        """
        cfg = RunPoolConfig(
            max_concurrency=50,
            initial_concurrency=10,
            min_concurrency=1,
            hysteresis_checks=3,
            ramp_up_factor=0.10,
            ramp_down_factor=0.25,
            critical_down_factor=0.50,
            rate_limit_burst_threshold=3,
            rate_limit_burst_window_s=60.0,
            rate_limit_down_factor=0.5,
            pressure_check_interval_s=60.0,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(cfg)

        # Both ceilings start at initial_concurrency.
        assert pool._provider_ceiling == cfg.initial_concurrency
        assert pool._memory_ceiling == cfg.initial_concurrency
        assert pool.current_max_concurrency == cfg.initial_concurrency

        # Run a full pressure sequence with zero 429s and record capacities.
        capacities = []

        # 3 NORMAL ticks → ramp-up (hysteresis=3).  Both governors ramp
        # symmetrically because there are no rate-limit events and no
        # pending retries.
        for _ in range(3):
            pool._adjust_concurrency(PressureLevel.NORMAL)
        capacities.append(("after_ramp_up", pool.current_max_concurrency))

        # HIGH tick → immediate memory cut.
        pool._adjust_concurrency(PressureLevel.HIGH)
        capacities.append(("after_high", pool.current_max_concurrency))

        # CRITICAL tick → aggressive memory cut.
        pool._adjust_concurrency(PressureLevel.CRITICAL)
        capacities.append(("after_critical", pool.current_max_concurrency))

        # 3 more NORMAL ticks → recovery ramp.
        for _ in range(3):
            pool._adjust_concurrency(PressureLevel.NORMAL)
        capacities.append(("after_recovery", pool.current_max_concurrency))

        # No recent rate limits tracked — provider governor never throttled.
        assert len(pool._recent_rate_limits) == 0

        # Provider ceiling must be >= memory ceiling at all times when there
        # are zero 429s, so the effective target is purely memory-driven.
        assert pool._provider_ceiling >= pool._memory_ceiling, (
            f"provider_ceiling ({pool._provider_ceiling}) should be >= "
            f"memory_ceiling ({pool._memory_ceiling}) with zero 429s"
        )
        assert pool.current_max_concurrency == pool._memory_ceiling

        # Verify the capacity trajectory is purely memory-driven:
        # ramp-up: both ceilings 10 + 10% = 11, effective = 11
        assert capacities[0] == ("after_ramp_up", 11)
        # HIGH: memory 11 - ceil(11*0.25)=3 → 8; provider still >= 8
        assert capacities[1] == ("after_high", 8)
        # CRITICAL: memory 8 - ceil(8*0.50)=4 → 4; provider still >= 4
        assert capacities[2] == ("after_critical", 4)
        # 3 NORMAL ticks → memory ramp: 4 + max(1, int(4*0.10))=1 → 5
        assert capacities[3] == ("after_recovery", 5)


class TestQuotaPauseResume:
    """End-to-end coverage for the QuotaExhaustedError-triggered pool pause
    This covers the dispatch-robustness contract.

    Scenario: a quota signal lands mid-cohort, pool pauses until the named
    reset_at + buffer, queued submissions block during the pause, no tickers
    are marked permanent_failure, then submissions resume cleanly.
    """

    def test_pause_blocks_submissions_until_reset(self, pool_config):
        """Submissions made while the pool is paused don't run until pause lifts."""

        async def _run():
            pool = RunPool(pool_config)
            # Pause for 1s from now — short enough for fast test.
            reset_at = datetime.now(UTC) + timedelta(seconds=1.0)
            pool.pause_for_quota(reset_at, buffer_s=0.2, progress_interval_s=0.5)
            # Submit a process — it should NOT run until the pause lifts.
            config = ProcessConfig(
                launch=PreparedLaunch(command=("echo", "after-pause")),
                label="paused-task",
            )
            start = asyncio.get_event_loop().time()
            future = pool.submit(config)
            result = await asyncio.wait_for(future, timeout=10.0)
            elapsed = asyncio.get_event_loop().time() - start
            assert result.exit_code == 0
            # The pause + buffer ≈ 1.2s; allow generous slack for CI noise.
            assert elapsed >= 1.0, f"submission ran too early ({elapsed:.2f}s)"
            await pool.shutdown()

        asyncio.run(_run())

    def test_pause_resumes_after_reset_window(self, pool_config):
        """After the reset_at + buffer, the pause is cleared and the gate opens."""

        async def _run():
            pool = RunPool(pool_config)
            reset_at = datetime.now(UTC) + timedelta(seconds=0.5)
            pool.pause_for_quota(reset_at, buffer_s=0.2, progress_interval_s=0.5)
            assert pool._quota_paused_until is not None
            # Wait long enough for the pause task to clear state.
            await asyncio.sleep(1.5)
            assert pool._quota_paused_until is None
            await pool.shutdown()

        asyncio.run(_run())

    def test_record_failure_class_quota_with_reset_triggers_pause(self, pool_config):
        """The pool pauses when record_failure_class("quota_exhausted",
        quota_reset_at=...) lands — the integration point for the orchestrator
        to wire up after classify_failure spots the quota pattern. Verifies the
        state transition; pause/resume timing is covered by the dedicated tests
        above."""

        async def _run():
            pool = RunPool(pool_config)
            reset_at = datetime.now(UTC) + timedelta(seconds=0.5)
            pool.record_failure_class("quota_exhausted", quota_reset_at=reset_at)
            assert pool._quota_paused_until == reset_at
            assert pool._failure_class_counts.get("quota_exhausted") == 1
            # Cancel the pause task so shutdown doesn't wait for the 30s default
            # buffer to expire.
            if pool._quota_pause_task is not None:
                pool._quota_pause_task.cancel()
            await pool.shutdown()

        asyncio.run(_run())

    def test_record_failure_class_quota_without_reset_does_not_pause(self, pool_config):
        """When classify_failure returns quota_exhausted but we couldn't parse
        a reset time (e.g. message format changed), don't pause — pause without
        a known reset would be indefinite. The count is still recorded."""

        async def _run():
            pool = RunPool(pool_config)
            pool.record_failure_class("quota_exhausted")
            assert pool._quota_paused_until is None
            assert pool._failure_class_counts.get("quota_exhausted") == 1
            await pool.shutdown()

        asyncio.run(_run())
