"""RunPool cleanup and cause preservation at launch lifecycle boundaries."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override

import pytest

from metaproc.runpool.backend import LaunchHandle, LocalBackend, PreparedLaunch
from metaproc.runpool.host_admission import HostAdmissionLease
from metaproc.runpool.pool import ProcessConfig, RunPool, RunPoolConfig


class RecordingLocalBackend(LocalBackend):
    """Use real children while exposing their handles and cleanup order."""

    def __init__(self, lifecycle: list[str]) -> None:
        self.handles: list[LaunchHandle] = []
        self.lifecycle = lifecycle

    @override
    async def launch(self, prepared: PreparedLaunch, label: str = "") -> LaunchHandle:
        handle = await super().launch(prepared, label)
        self.handles.append(handle)
        return handle

    @override
    async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
        self.lifecycle.append("kill")
        await super().kill(handle, sig)


def _sleeping_process() -> ProcessConfig:
    return ProcessConfig(
        launch=PreparedLaunch(command=(sys.executable, "-c", "import time; time.sleep(60)")),
        label="lifecycle-failure",
        lane_id="test-lane",
        execution_profile="test-lane",
    )


async def _assert_capacity_reusable(pool: RunPool, external: asyncio.Semaphore) -> None:
    assert pool._pending_count == 0
    assert pool.active_count == 0
    assert pool._semaphore.held == 0
    assert pool._semaphore.available == pool._semaphore.capacity
    assert all(counters["active"] == 0 for counters in pool._lane_counters.values())
    await asyncio.wait_for(external.acquire(), timeout=1)
    external.release()


def _run_failure_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inject: Callable[[RunPool, list[str]], None],
    expected: str,
) -> None:
    async def run() -> None:
        lifecycle: list[str] = []
        external = asyncio.Semaphore(1)
        backend = RecordingLocalBackend(lifecycle)
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=1,
                initial_concurrency=1,
                monitor_interval_s=0.01,
                pressure_check_interval_s=60,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
                external_semaphore=external,
                host_admission_enabled=True,
                host_admission_dir=tmp_path / "host-slots",
                host_admission_limit=1,
                execution_profile="test-lane",
            ),
            backend=backend,
        )
        assert pool._host_admission is not None
        original_release = pool._host_admission.release

        def record_release(lease: HostAdmissionLease) -> None:
            lifecycle.append("release")
            original_release(lease)

        monkeypatch.setattr(pool._host_admission, "release", record_release)
        inject(pool, lifecycle)
        try:
            with pytest.raises(OSError, match=expected):
                await pool.submit(_sleeping_process())
            await _assert_capacity_reusable(pool, external)
            assert not any((tmp_path / "host-slots").rglob("lease.json"))
            if backend.handles:
                assert lifecycle.index("kill") < lifecycle.index("release")
                assert backend.handles[0]._process is not None
                assert backend.handles[0]._process.returncode is not None
        finally:
            for handle in backend.handles:
                process = handle._process
                if process is not None and process.returncode is None:
                    with contextlib.suppress(BaseException):
                        await backend.kill(handle)
            with contextlib.suppress(BaseException):
                await pool.shutdown(timeout_s=0)

    asyncio.run(run())


def test_record_child_failure_kills_child_and_releases_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def inject(pool: RunPool, lifecycle: list[str]) -> None:
        del lifecycle
        assert pool._host_admission is not None

        def fail_record_child(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("record child failed")

        monkeypatch.setattr(pool._host_admission, "record_child", fail_record_child)

    _run_failure_case(tmp_path, monkeypatch, inject, "record child failed")


def test_process_start_failure_preserves_cause_when_terminal_logging_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def inject(pool: RunPool, lifecycle: list[str]) -> None:
        del lifecycle
        assert pool._event_logger is not None

        def fail_process_start(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("process start failed")

        def fail_process_kill(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("process kill log failed")

        monkeypatch.setattr(pool._event_logger, "process_start", fail_process_start)
        monkeypatch.setattr(pool._event_logger, "process_kill", fail_process_kill)

    _run_failure_case(tmp_path, monkeypatch, inject, "process start failed")


def test_host_slot_acquired_logging_failure_releases_unreturned_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def inject(pool: RunPool, lifecycle: list[str]) -> None:
        del lifecycle
        assert pool._event_logger is not None

        def fail_host_slot_acquired(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("host slot acquired log failed")

        monkeypatch.setattr(pool._event_logger, "host_slot_acquired", fail_host_slot_acquired)

    _run_failure_case(tmp_path, monkeypatch, inject, "host slot acquired log failed")


def test_host_slot_released_logging_failure_does_not_leak_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        external = asyncio.Semaphore(1)
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=1,
                initial_concurrency=1,
                monitor_interval_s=0.01,
                pressure_check_interval_s=60,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
                external_semaphore=external,
                host_admission_enabled=True,
                host_admission_dir=tmp_path / "host-slots",
                host_admission_limit=1,
                execution_profile="test-lane",
            )
        )
        assert pool._event_logger is not None

        def fail_host_slot_released(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("host slot released log failed")

        monkeypatch.setattr(pool._event_logger, "host_slot_released", fail_host_slot_released)
        try:
            result = await asyncio.wait_for(
                pool.submit(
                    ProcessConfig(
                        launch=PreparedLaunch(command=(sys.executable, "-c", "pass")),
                        label="release-log-failure",
                        lane_id="test-lane",
                        execution_profile="test-lane",
                    )
                ),
                timeout=2,
            )
            assert result.exit_code == 0
            await _assert_capacity_reusable(pool, external)
            assert not any((tmp_path / "host-slots").rglob("lease.json"))
        finally:
            with contextlib.suppress(BaseException):
                await pool.shutdown(timeout_s=0)

    asyncio.run(run())


def test_context_manager_shutdown_failure_does_not_mask_body_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=1,
                initial_concurrency=1,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )

        async def fail_shutdown(timeout_s: float | None = None) -> None:
            del timeout_s
            raise OSError("shutdown cleanup failed")

        monkeypatch.setattr(pool, "shutdown", fail_shutdown)
        with pytest.raises(RuntimeError, match="body failed") as caught:
            async with pool:
                raise RuntimeError("body failed")
        assert any("shutdown cleanup failed" in note for note in caught.value.__notes__)

    asyncio.run(run())


def test_shutdown_owns_quota_pause_timer_and_refuses_a_new_one(tmp_path: Path) -> None:
    async def run() -> None:
        pool = RunPool(
            RunPoolConfig(
                max_concurrency=1,
                initial_concurrency=1,
                state_dir=tmp_path / "state",
                logs_dir=tmp_path / "logs",
            )
        )
        pool.pause_for_quota(
            datetime.now(UTC) + timedelta(hours=1),
            progress_interval_s=3600,
        )
        timer = pool._quota_pause_task
        assert timer is not None
        await asyncio.sleep(0)
        try:
            await pool.shutdown(timeout_s=0)
            assert timer.done()
            pool.pause_for_quota(
                datetime.now(UTC) + timedelta(hours=2),
                progress_interval_s=3600,
            )
            assert pool._quota_pause_task is timer
        finally:
            current = pool._quota_pause_task
            if current is not None and not current.done():
                current.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await current

    asyncio.run(run())
