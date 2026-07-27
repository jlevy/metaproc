"""Tests for RunPoolStatus models and file I/O."""

from __future__ import annotations

import os
from pathlib import Path

from metaproc.osutils.memory_pressure import PressureLevel
from metaproc.runpool.status import (
    ControllerStatus,
    PressureStatus,
    ProcessStatus,
    RunPoolStatus,
    ScaleBounds,
    ScaleOverride,
    ScaleState,
    is_pool_alive,
    read_scale_override,
    read_scale_state,
    read_status,
    write_scale_override,
    write_scale_state,
    write_status,
)


def _sample_status() -> RunPoolStatus:
    return RunPoolStatus(
        pool_id="pool-test-123",
        pid=os.getpid(),
        started_at="2026-04-03T22:15:00",
        updated_at="2026-04-03T22:17:30",
        backend="local",
        max_concurrency=20,
        current_concurrency=12,
        active_count=3,
        pending_count=5,
        completed_count=10,
        failed_count=1,
        killed_count=0,
        pressure=PressureStatus(
            level=PressureLevel.NORMAL,
            available_pct=55.0,
            swap_used_gb=1.2,
            total_memory_gb=32.0,
            source="macos-memorystatus",
        ),
        controller=ControllerStatus(
            mode="adaptive",
            operator_cap=20,
            effective_target=12,
            memory_ceiling=20,
            provider_ceiling=12,
            bottleneck="DSQ-bound",
            recent_rate_limits=1,
            pending_retries=2,
        ),
        processes=[
            ProcessStatus(
                pid=12345,
                backend="local",
                label="test-process",
                started_at="2026-04-03T22:15:05",
                elapsed_s=145.3,
                rss_bytes=524288000,
                descendants=3,
                status="running",
            ),
        ],
        recent_completions=[
            ProcessStatus(
                pid=12340,
                backend="local",
                label="done-process",
                started_at="2026-04-03T22:14:00",
                elapsed_s=98.7,
                rss_bytes=0,
                status="completed",
                exit_code=0,
                peak_rss_bytes=389120000,
            ),
        ],
    )


class TestRunPoolStatus:
    def test_model_roundtrip(self):
        status = _sample_status()
        data = status.model_dump(mode="json")
        restored = RunPoolStatus.model_validate(data)
        assert restored.pool_id == status.pool_id
        assert restored.active_count == 3
        assert restored.pressure.level == PressureLevel.NORMAL
        assert restored.controller is not None
        assert restored.controller.bottleneck == "DSQ-bound"
        assert len(restored.processes) == 1
        assert len(restored.recent_completions) == 1

    def test_write_and_read(self, tmp_path: Path):
        status = _sample_status()
        path = tmp_path / "runpool-status.yaml"
        write_status(path, status)

        assert path.exists()
        restored = read_status(path)
        assert restored.pool_id == "pool-test-123"
        assert restored.active_count == 3
        assert restored.pressure.available_pct == 55.0
        assert restored.controller is not None
        assert restored.controller.provider_ceiling == 12
        assert len(restored.processes) == 1
        assert restored.processes[0].label == "test-process"

    def test_read_status_accepts_historical_missing_total_memory(self, tmp_path: Path):
        path = tmp_path / "runpool-status.yaml"
        path.write_text(
            """
pool_id: pool-before-total-memory
pid: 999999999
started_at: '2026-05-17T13:00:00'
updated_at: '2026-05-17T13:05:00'
backend: local
max_concurrency: 2
current_concurrency: 1
active_count: 0
pending_count: 0
completed_count: 1
failed_count: 0
killed_count: 0
pressure:
  level: normal
  available_pct: 55.0
  swap_used_gb: 4.0
  source: macos-memorystatus
""".lstrip()
        )

        restored = read_status(path)

        assert restored.pressure.available_pct == 55.0
        assert restored.pressure.total_memory_gb is None

    def test_atomic_write_creates_parents(self, tmp_path: Path):
        path = tmp_path / "deep" / "nested" / "runpool-status.yaml"
        write_status(path, _sample_status())
        assert path.exists()

    def test_is_pool_alive_current_process(self):
        status = _sample_status()
        # Our own PID should be alive.
        assert is_pool_alive(status) is True

    def test_is_pool_alive_dead_process(self):
        status = _sample_status()
        # Use an impossible PID.
        dead_status = status.model_copy(update={"pid": 999999999})
        assert is_pool_alive(dead_status) is False

    def test_recent_completions_preserved_on_roundtrip(self, tmp_path: Path):
        status = _sample_status()
        # Add more completions.
        for i in range(5):
            status.recent_completions.append(
                ProcessStatus(
                    pid=10000 + i,
                    backend="local",
                    label=f"process-{i}",
                    started_at="",
                    elapsed_s=10.0,
                    status="completed",
                    exit_code=0,
                )
            )
        path = tmp_path / "runpool-status.yaml"
        write_status(path, status)
        restored = read_status(path)
        assert len(restored.recent_completions) == 6  # 1 original + 5 new

    def test_scale_override_roundtrip_with_max_concurrency(self, tmp_path: Path):
        """ScaleOverride supports both operator_cap and max_concurrency fields."""

        override = ScaleOverride(mode="adaptive", operator_cap=16, max_concurrency=20)
        path = tmp_path / "scale-override.yaml"
        write_scale_override(path, override)
        restored = read_scale_override(path)
        assert restored.mode == "adaptive"
        assert restored.operator_cap == 16
        assert restored.max_concurrency == 20

    def test_scale_override_max_concurrency_optional(self, tmp_path: Path):
        """max_concurrency defaults to None (backward compatible with operator_cap-only)."""

        override = ScaleOverride(operator_cap=12)
        path = tmp_path / "scale-override.yaml"
        write_scale_override(path, override)
        restored = read_scale_override(path)
        assert restored.operator_cap == 12
        assert restored.max_concurrency is None
        assert restored.mode is None

    def test_scale_state_roundtrip(self, tmp_path: Path):
        scale_state = ScaleState(
            updated_at="2026-04-03T22:17:30",
            controller=ControllerStatus(
                mode="adaptive",
                operator_cap=30,
                effective_target=18,
                memory_ceiling=24,
                provider_ceiling=18,
                bottleneck="DSQ-bound",
                recent_rate_limits=2,
                pending_retries=1,
            ),
            desired_workers=2,
            desired_max_concurrency=15,
            generation=3,
            bounds=ScaleBounds(
                min_workers=1,
                max_workers=4,
                min_concurrency=1,
                max_concurrency=30,
            ),
        )
        path = tmp_path / "scale-state.yaml"
        write_scale_state(path, scale_state)
        restored = read_scale_state(path)
        assert restored.controller.effective_target == 18
        assert restored.controller.bottleneck == "DSQ-bound"
        assert restored.desired_workers == 2
        assert restored.desired_max_concurrency == 15
        assert restored.generation == 3
        assert restored.bounds is not None
        assert restored.bounds.max_workers == 4
