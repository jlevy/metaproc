"""Tests for LocalBackend."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from metaproc.runpool.backend import (
    HealthMetrics,
    LaunchHandle,
    LocalBackend,
    PreparedLaunch,
    get_log_size,
    launch_and_supervise,
    read_log_tail_sync,
)


@pytest.fixture
def backend():
    return LocalBackend()


class TestLocalBackend:
    def test_name(self, backend):
        assert backend.name == "local"

    def test_launch_and_poll(self, backend):
        async def _run():
            prepared = PreparedLaunch(command=("echo", "hello"))
            handle = await backend.launch(prepared)
            assert handle.pid is not None
            assert handle.backend_name == "local"
            # Wait for completion.
            for _ in range(20):
                exit_code = await backend.poll(handle)
                if exit_code is not None:
                    assert exit_code == 0
                    return
                await asyncio.sleep(0.1)
            pytest.fail("Process did not exit within 2 seconds")

        asyncio.run(_run())

    def test_launch_with_log_path(self, backend, tmp_path: Path):
        async def _run():
            log_path = tmp_path / "output.log"
            prepared = PreparedLaunch(
                command=("echo", "hello world"),
                log_path=log_path,
            )
            handle = await backend.launch(prepared)
            # Wait for completion.
            for _ in range(20):
                if await backend.poll(handle) is not None:
                    break
                await asyncio.sleep(0.1)
            assert log_path.exists()
            content = log_path.read_text()
            assert "hello world" in content

        asyncio.run(_run())

    def test_launch_with_env(self, backend):
        async def _run():
            prepared = PreparedLaunch(
                command=(sys.executable, "-c", "import os; print(os.environ['TEST_VAR'])"),
                env={"TEST_VAR": "test_value"},
            )
            handle = await backend.launch(prepared)
            for _ in range(20):
                if await backend.poll(handle) is not None:
                    break
                await asyncio.sleep(0.1)

        asyncio.run(_run())

    def test_launch_with_cwd(self, backend, tmp_path: Path):
        async def _run():
            log_path = tmp_path / "output.log"
            prepared = PreparedLaunch(
                command=(sys.executable, "-c", "import os; print(os.getcwd())"),
                cwd=tmp_path,
                log_path=log_path,
            )
            handle = await backend.launch(prepared)
            for _ in range(20):
                if await backend.poll(handle) is not None:
                    break
                await asyncio.sleep(0.1)
            assert str(tmp_path) in log_path.read_text()

        asyncio.run(_run())

    def test_kill_sigterm(self, backend):
        async def _run():
            prepared = PreparedLaunch(command=("sleep", "60"))
            handle = await backend.launch(prepared)
            await asyncio.sleep(0.2)  # Let it start.
            await backend.kill(handle)
            # Should be dead now.
            for _ in range(20):
                exit_code = await backend.poll(handle)
                if exit_code is not None:
                    return
                await asyncio.sleep(0.1)
            pytest.fail("Process did not die after kill")

        asyncio.run(_run())

    def test_kill_already_exited(self, backend):
        async def _run():
            prepared = PreparedLaunch(command=("true",))
            handle = await backend.launch(prepared)
            await asyncio.sleep(0.3)
            # Process already exited — kill should be a no-op.
            await backend.kill(handle)

        asyncio.run(_run())

    def test_health_metrics(self, backend):
        async def _run():
            prepared = PreparedLaunch(command=("sleep", "10"))
            handle = await backend.launch(prepared)
            await asyncio.sleep(0.3)
            metrics = await backend.health(handle)
            assert metrics is not None
            assert metrics.rss_bytes is not None
            assert metrics.rss_bytes > 0
            assert metrics.descendants is not None
            assert metrics.descendants >= 0
            await backend.kill(handle)

        asyncio.run(_run())

    def test_health_dead_process(self, backend):
        async def _run():
            prepared = PreparedLaunch(command=("true",))
            handle = await backend.launch(prepared)
            await asyncio.sleep(0.3)
            # Process exited — health returns None.
            metrics = await backend.health(handle)
            assert metrics is None

        asyncio.run(_run())

    def test_cancel_during_launch_kills_late_handle_before_return(self):
        class DelayedBackend:
            name = "delayed"

            def __init__(self) -> None:
                self.launch_started = asyncio.Event()
                self.allow_launch = asyncio.Event()
                self.killed = False

            async def launch(
                self,
                prepared: PreparedLaunch,
                label: str = "",
            ) -> LaunchHandle:
                del prepared, label
                self.launch_started.set()
                await self.allow_launch.wait()
                return LaunchHandle(pid=123, backend_name=self.name)

            async def poll(self, handle: LaunchHandle) -> int | None:
                del handle
                return 0 if self.killed else None

            async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
                del handle, sig
                self.killed = True

            async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
                del handle
                return None

            async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
                del handle, lines
                return ""

        async def _run() -> None:
            delayed = DelayedBackend()
            task = asyncio.create_task(
                launch_and_supervise(
                    delayed,
                    PreparedLaunch(command=("unused",)),
                    timeout_s=None,
                )
            )
            await delayed.launch_started.wait()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            delayed.allow_launch.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert delayed.killed is True

        asyncio.run(_run())


class TestMetadata:
    """Cloud metadata on PreparedLaunch and LaunchHandle."""

    def test_prepared_launch_metadata_default_none(self):
        pl = PreparedLaunch(command=("echo",))
        assert pl.metadata is None

    def test_prepared_launch_metadata_set(self):
        meta = {"run_id": "mine-test-15", "item_id": "AAPL-2024Q3", "variant": "pi-deepseek"}
        pl = PreparedLaunch(command=("echo",), metadata=meta)
        assert pl.metadata == meta
        assert pl.metadata is not None
        assert pl.metadata["run_id"] == "mine-test-15"

    def test_launch_handle_metadata_default_none(self):
        handle = LaunchHandle(pid=123, backend_name="local")
        assert handle.metadata is None

    def test_launch_handle_metadata_set(self):
        meta = {"job_name": "batch-xyz", "task_index": "42"}
        handle = LaunchHandle(external_id="batch-xyz/42", backend_name="mock", metadata=meta)
        assert handle.metadata == meta
        assert handle.pid is None
        assert handle.external_id == "batch-xyz/42"

    def test_local_backend_ignores_metadata(self, backend):
        """LocalBackend works fine when metadata is present — it just ignores it."""

        async def _run():
            prepared = PreparedLaunch(
                command=("echo", "hello"),
                metadata={"run_id": "test", "item_id": "foo"},
            )
            handle = await backend.launch(prepared)
            assert handle.pid is not None
            assert handle.metadata is None  # LocalBackend doesn't propagate metadata
            for _ in range(20):
                exit_code = await backend.poll(handle)
                if exit_code is not None:
                    assert exit_code == 0
                    return
                await asyncio.sleep(0.1)

        asyncio.run(_run())


class TestLogUtilities:
    def test_get_log_size(self, tmp_path: Path):
        log_file = tmp_path / "test.log"
        log_file.write_text("hello world\n")
        size = get_log_size(log_file)
        assert size is not None
        assert size == 12

    def test_get_log_size_none(self):
        assert get_log_size(None) is None

    def test_get_log_size_missing_file(self, tmp_path: Path):
        assert get_log_size(tmp_path / "nonexistent.log") is None

    def test_read_log_tail_sync(self, tmp_path: Path):
        log_file = tmp_path / "test.log"
        log_file.write_text("\n".join(f"line {i}" for i in range(100)))
        tail = read_log_tail_sync(log_file, lines=5)
        assert "line 99" in tail
        assert "line 95" in tail
        assert "line 90" not in tail

    def test_read_log_tail_sync_none(self):
        assert read_log_tail_sync(None) == ""
