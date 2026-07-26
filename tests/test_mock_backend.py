"""Tests for MockBackend — deterministic in-memory backend."""

from __future__ import annotations

import asyncio

from metaproc.paths import RUNPOOL_EVENTS_FILE
from metaproc.runpool.backend import PreparedLaunch
from metaproc.runpool.mock_backend import MockBackend, MockBehavior
from metaproc.runpool.pool import ProcessConfig, RunPool, RunPoolConfig
from metaproc.runpool.registry import available_backends, get_backend, reset_registry


class TestMockBackendLifecycle:
    def test_launch_returns_handle(self):
        backend = MockBackend()
        handle = asyncio.run(backend.launch(PreparedLaunch(command=("echo", "hi")), label="item-1"))
        assert handle.external_id == "mock-item-1"
        assert handle.backend_name == "mock"
        assert handle.pid is None

    def test_poll_returns_none_during_execution(self):
        backend = MockBackend(default_behavior=MockBehavior(run_duration_s=10.0))
        handle = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="slow"))
        result = asyncio.run(backend.poll(handle))
        assert result is None  # Still running

    def test_poll_returns_exit_code_after_duration(self):
        backend = MockBackend(default_behavior=MockBehavior(run_duration_s=0.0, exit_code=0))
        handle = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="fast"))
        result = asyncio.run(backend.poll(handle))
        assert result == 0

    def test_poll_returns_configured_exit_code(self):
        backend = MockBackend(behaviors={"fail": MockBehavior(exit_code=1, run_duration_s=0.0)})
        handle = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="fail"))
        result = asyncio.run(backend.poll(handle))
        assert result == 1

    def test_kill_causes_sigkill_exit(self):
        backend = MockBackend(default_behavior=MockBehavior(run_duration_s=10.0))
        handle = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="killme"))
        asyncio.run(backend.kill(handle))
        result = asyncio.run(backend.poll(handle))
        assert result == 137

    def test_health_returns_metrics(self):
        backend = MockBackend(default_behavior=MockBehavior(rss_bytes=500_000, descendants=3))
        handle = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="health"))
        metrics = asyncio.run(backend.health(handle))
        assert metrics is not None
        assert metrics.rss_bytes == 500_000
        assert metrics.descendants == 3

    def test_health_returns_none_after_kill(self):
        backend = MockBackend()
        handle = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="dead"))
        asyncio.run(backend.kill(handle))
        metrics = asyncio.run(backend.health(handle))
        assert metrics is None

    def test_read_log_tail(self):
        backend = MockBackend(default_behavior=MockBehavior(log_output="line 1\nline 2"))
        handle = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="log"))
        output = asyncio.run(backend.read_log_tail(handle))
        assert output == "line 1\nline 2"

    def test_calls_tracking(self):
        backend = MockBackend()
        handle = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="track"))
        asyncio.run(backend.poll(handle))
        asyncio.run(backend.health(handle))
        asyncio.run(backend.kill(handle))
        asyncio.run(backend.read_log_tail(handle))

        methods = [c[0] for c in backend.calls]
        assert methods == ["launch", "poll", "health", "kill", "read_log_tail"]
        # All calls should have the label
        labels = [c[1] for c in backend.calls]
        assert all(label == "track" for label in labels)

    def test_per_label_behaviors(self):
        backend = MockBackend(
            behaviors={
                "ok": MockBehavior(exit_code=0, run_duration_s=0.0),
                "bad": MockBehavior(exit_code=42, run_duration_s=0.0),
            }
        )
        h_ok = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="ok"))
        h_bad = asyncio.run(backend.launch(PreparedLaunch(command=("echo",)), label="bad"))
        assert asyncio.run(backend.poll(h_ok)) == 0
        assert asyncio.run(backend.poll(h_bad)) == 42


class TestMockBackendWithRunPool:
    def test_submit_batch_all_succeed(self, tmp_path):
        """Submit 5 items through RunPool with MockBackend, all succeed."""
        backend = MockBackend(default_behavior=MockBehavior(exit_code=0, run_duration_s=0.0))
        config = RunPoolConfig(
            max_concurrency=3,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(config, backend=backend)

        configs = [
            ProcessConfig(
                label=f"item-{i}",
                launch=PreparedLaunch(command=("echo", str(i))),
            )
            for i in range(5)
        ]

        async def run():
            results = await pool.submit_batch(configs)
            await pool.shutdown()
            return results

        results = asyncio.run(run())
        assert len(results) == 5
        assert all(r.exit_code == 0 for r in results)
        assert all(r.backend == "mock" for r in results)
        assert all(r.external_id is not None for r in results)

    def test_submit_batch_mixed_results(self, tmp_path):
        """Some items succeed, some fail."""
        backend = MockBackend(
            behaviors={
                "item-0": MockBehavior(exit_code=0, run_duration_s=0.0),
                "item-1": MockBehavior(exit_code=1, run_duration_s=0.0),
                "item-2": MockBehavior(exit_code=0, run_duration_s=0.0),
                "item-3": MockBehavior(exit_code=2, run_duration_s=0.0),
                "item-4": MockBehavior(exit_code=0, run_duration_s=0.0),
            }
        )
        config = RunPoolConfig(
            max_concurrency=5,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(config, backend=backend)

        configs = [
            ProcessConfig(
                label=f"item-{i}",
                launch=PreparedLaunch(command=("echo", str(i))),
            )
            for i in range(5)
        ]

        async def run():
            results = await pool.submit_batch(configs)
            await pool.shutdown()
            return results

        results = asyncio.run(run())
        exit_codes = {r.config.label: r.exit_code for r in results}
        assert exit_codes["item-0"] == 0
        assert exit_codes["item-1"] == 1
        assert exit_codes["item-2"] == 0
        assert exit_codes["item-3"] == 2
        assert exit_codes["item-4"] == 0

    def test_event_log_written_with_mock(self, tmp_path):
        """Verify event log includes mock backend info."""
        backend = MockBackend(default_behavior=MockBehavior(exit_code=0, run_duration_s=0.0))
        config = RunPoolConfig(
            max_concurrency=2,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(config, backend=backend)

        configs = [
            ProcessConfig(
                label=f"item-{i}",
                launch=PreparedLaunch(command=("echo",)),
            )
            for i in range(3)
        ]

        async def run():
            await pool.submit_batch(configs)
            await pool.shutdown()

        asyncio.run(run())

        events_file = tmp_path / "logs" / RUNPOOL_EVENTS_FILE
        assert events_file.exists()
        content = events_file.read_text()
        assert "mock" in content

    def test_status_file_with_mock(self, tmp_path):
        """Verify status file records mock backend."""
        backend = MockBackend(default_behavior=MockBehavior(exit_code=0, run_duration_s=0.0))
        config = RunPoolConfig(
            max_concurrency=2,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(config, backend=backend)

        configs = [
            ProcessConfig(
                label="single",
                launch=PreparedLaunch(command=("echo",)),
            )
        ]

        async def run():
            await pool.submit_batch(configs)
            await pool.shutdown()

        asyncio.run(run())

        status_file = tmp_path / "state" / "runpool-status.yaml"
        assert status_file.exists()
        content = status_file.read_text()
        assert "mock" in content


class TestMockBackendRegistry:
    def test_mock_registered(self):
        reset_registry()
        try:
            backend = get_backend("mock")
            assert backend.name == "mock"
            assert isinstance(backend, MockBackend)
        finally:
            reset_registry()

    def test_mock_in_available_list(self):
        reset_registry()
        try:
            backends = available_backends()
            assert "mock" in backends
            assert "local" in backends
        finally:
            reset_registry()
