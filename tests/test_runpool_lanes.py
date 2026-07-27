"""Tests for lane-aware runpool plumbing.

Validates that ProcessConfig.lane_id, RunPoolConfig.execution_lanes, and
the per-lane status counters round-trip cleanly. Phase 2 of the
execution-lanes spec.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from metaproc.models.lane import ExecutionLane
from metaproc.runpool.backend import PreparedLaunch
from metaproc.runpool.mock_backend import MockBackend, MockBehavior
from metaproc.runpool.pool import ProcessConfig, RunPool, RunPoolConfig
from metaproc.runpool.status import LaneStatus, RunPoolStatus


def _mock_config(label: str, *, lane_id: str | None = None) -> ProcessConfig:
    """Build a ProcessConfig wired for MockBackend.

    The MockBackend reads exit_code from MockBehavior keyed by label, not
    from the launch command — callers configure failures by passing a
    ``behaviors={label: MockBehavior(exit_code=N)}`` dict to MockBackend.
    """
    return ProcessConfig(
        launch=PreparedLaunch(command=("/bin/true",)),
        label=label,
        lane_id=lane_id,
        execution_profile=lane_id,
    )


def _mock_backend(**label_exit_codes: int) -> MockBackend:
    return MockBackend(
        behaviors={label: MockBehavior(exit_code=code) for label, code in label_exit_codes.items()}
    )


class TestRunPoolConfigLanes:
    def test_lanes_default_to_empty(self) -> None:
        config = RunPoolConfig()
        assert config.execution_lanes == []

    def test_register_lanes(self) -> None:
        lanes = [
            ExecutionLane(
                lane_id="codex-gpt55",
                execution_profile="codex-gpt55",
                adapter="codex-cli",
                comparison_role="primary",
            ),
            ExecutionLane(
                lane_id="claude-opus",
                execution_profile="claude-opus",
                adapter="claude-code-cli",
                comparison_role="comparison",
            ),
        ]
        config = RunPoolConfig(execution_lanes=lanes)
        assert len(config.execution_lanes) == 2


class TestProcessConfigLanes:
    def test_lane_id_defaults_to_none(self) -> None:
        config = ProcessConfig(
            launch=PreparedLaunch(command=("/bin/true",)),
        )
        assert config.lane_id is None
        assert config.execution_profile is None

    def test_lane_id_propagates(self) -> None:
        config = ProcessConfig(
            launch=PreparedLaunch(command=("/bin/true",)),
            lane_id="codex-gpt55",
            execution_profile="codex-gpt55",
        )
        assert config.lane_id == "codex-gpt55"
        assert config.execution_profile == "codex-gpt55"


class TestLaneStatusModel:
    def test_round_trip(self) -> None:
        row = LaneStatus(
            lane_id="codex-gpt55",
            execution_profile="codex-gpt55",
            adapter="codex-cli",
            comparison_role="primary",
            active_count=2,
            completed_count=4,
            failed_count=1,
        )
        data = row.model_dump()
        assert LaneStatus.model_validate(data) == row


class TestPoolLaneRegistry:
    def test_degenerate_synthesizes_single_lane(self, tmp_path: Path) -> None:
        config = RunPoolConfig(
            execution_profile="codex-gpt55",
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(config, backend=MockBackend())
        snapshot = pool.snapshot
        assert isinstance(snapshot, RunPoolStatus)
        assert len(snapshot.lanes) == 1
        lane = snapshot.lanes[0]
        assert lane.lane_id == "codex-gpt55"
        assert lane.execution_profile == "codex-gpt55"
        assert lane.active_count == 0

    def test_registered_lanes_appear_in_snapshot(self, tmp_path: Path) -> None:
        lanes = [
            ExecutionLane(
                lane_id="codex-gpt55",
                execution_profile="codex-gpt55",
                adapter="codex-cli",
                comparison_role="primary",
            ),
            ExecutionLane(
                lane_id="claude-opus",
                execution_profile="claude-opus",
                adapter="claude-code-cli",
                comparison_role="comparison",
            ),
        ]
        config = RunPoolConfig(
            execution_lanes=lanes,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(config, backend=MockBackend())
        snapshot = pool.snapshot
        assert [lane.lane_id for lane in snapshot.lanes] == ["codex-gpt55", "claude-opus"]
        assert snapshot.lanes[0].adapter == "codex-cli"
        assert snapshot.lanes[1].comparison_role == "comparison"

    def test_completion_updates_per_lane_counters(self, tmp_path: Path) -> None:
        lanes = [
            ExecutionLane(
                lane_id="codex-gpt55",
                execution_profile="codex-gpt55",
                adapter="codex-cli",
            ),
            ExecutionLane(
                lane_id="claude-opus",
                execution_profile="claude-opus",
                adapter="claude-code-cli",
            ),
        ]
        config = RunPoolConfig(
            execution_lanes=lanes,
            max_concurrency=4,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )

        async def _run() -> RunPoolStatus:
            backend = _mock_backend(**{"claude-fail": 2})
            pool = RunPool(config, backend=backend)
            futures = [
                pool.submit(_mock_config("codex-1", lane_id="codex-gpt55")),
                pool.submit(_mock_config("codex-2", lane_id="codex-gpt55")),
                pool.submit(_mock_config("claude-fail", lane_id="claude-opus")),
            ]
            for fut in futures:
                await fut
            await pool.shutdown()
            return pool.snapshot

        snapshot = asyncio.run(_run())
        codex = next(lane for lane in snapshot.lanes if lane.lane_id == "codex-gpt55")
        claude = next(lane for lane in snapshot.lanes if lane.lane_id == "claude-opus")
        assert codex.completed_count == 2
        assert codex.failed_count == 0
        assert claude.completed_count == 0
        assert claude.failed_count == 1

    def test_unregistered_lane_id_gets_lazy_registered(self, tmp_path: Path) -> None:
        config = RunPoolConfig(
            execution_profile="codex-gpt55",
            max_concurrency=2,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )

        async def _run() -> RunPoolStatus:
            pool = RunPool(config, backend=MockBackend())
            fut = pool.submit(_mock_config("late-lane", lane_id="lazy-lane"))
            await fut
            await pool.shutdown()
            return pool.snapshot

        snapshot = asyncio.run(_run())
        lane_ids = [lane.lane_id for lane in snapshot.lanes]
        # Both the degenerate seed lane and the lazy lane should appear.
        assert "codex-gpt55" in lane_ids
        assert "lazy-lane" in lane_ids
        lazy = next(lane for lane in snapshot.lanes if lane.lane_id == "lazy-lane")
        assert lazy.completed_count == 1

    def test_completed_process_status_carries_lane_id(self, tmp_path: Path) -> None:
        config = RunPoolConfig(
            execution_profile="codex-gpt55",
            max_concurrency=2,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )

        async def _run() -> RunPoolStatus:
            pool = RunPool(config, backend=MockBackend())
            fut = pool.submit(_mock_config("task-1", lane_id="codex-gpt55"))
            await fut
            await pool.shutdown()
            return pool.snapshot

        snapshot = asyncio.run(_run())
        assert snapshot.recent_completions, "expected at least one recent completion"
        completion = snapshot.recent_completions[0]
        assert completion.lane_id == "codex-gpt55"
        assert completion.execution_profile == "codex-gpt55"


@pytest.mark.parametrize(
    ("exit_code", "expected_key"),
    [
        (0, "completed_count"),
        (1, "failed_count"),
    ],
)
def test_lane_counter_keys(tmp_path: Path, exit_code: int, expected_key: str) -> None:
    config = RunPoolConfig(
        execution_profile="codex-gpt55",
        max_concurrency=1,
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
    )

    async def _run() -> RunPoolStatus:
        backend = _mock_backend(task=exit_code)
        pool = RunPool(config, backend=backend)
        await pool.submit(_mock_config("task", lane_id="codex-gpt55"))
        await pool.shutdown()
        return pool.snapshot

    snapshot = asyncio.run(_run())
    lane = snapshot.lanes[0]
    assert getattr(lane, expected_key) == 1
