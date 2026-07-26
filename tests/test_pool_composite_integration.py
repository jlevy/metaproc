"""Integration test: composite-aware pool CLI commands over live RunPools.

Complements the file-shape regression in ``test_pool_composite.py`` by
driving **real** :class:`RunPool` instances into the canonical
composite-child layout, then invoking the four operator-facing
``metaproc pool`` subcommands (``status``, ``events``, ``health``,
``rollup``) against the parent run dir.

Layout produced (mirrors EIA dispatch + analysis-research):

    <parent>/.state/run-config.yaml         (V2 marker)
    <parent>/analysis-research/.state/run-config.yaml
    <parent>/analysis-research/.state/steps/<step_id>/runpool-status.yaml
    <parent>/analysis-research/.logs/runpool/steps/<step_id>/events.jsonl
    <parent>/analysis-research/.logs/runpool/steps/<step_id>/health.jsonl

The two per-step pools are real ``RunPool`` instances submitting short
``echo`` subprocesses, so the status / events / health files exposed to
the CLI are the same shape produced in production. Closes the
``internal-reference`` integration coverage gap.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc import paths as paths_mod
from metaproc.cli import app
from metaproc.runpool.backend import PreparedLaunch
from metaproc.runpool.pool import ProcessConfig, RunPool, RunPoolConfig

runner = CliRunner()


def _mark_v2(run_dir: Path) -> None:
    paths_mod.run_state_dir(run_dir).mkdir(parents=True, exist_ok=True)
    paths_mod.run_config_file(run_dir).write_text(
        f"metaproc_layout: {paths_mod.RUN_LAYOUT_VERSION}\n", encoding="utf-8"
    )


async def _drive_step_pool(
    child_run: Path,
    step_id: str,
    *,
    n_items: int,
    max_concurrency: int = 2,
) -> None:
    """Drive one real RunPool for *step_id* under *child_run*'s V2 layout."""
    state_dir = paths_mod.step_state_dir(child_run, step_id)
    logs_dir = paths_mod.runpool_step_logs_dir(child_run, step_id)
    config = RunPoolConfig(
        max_concurrency=max_concurrency,
        min_concurrency=1,
        initial_concurrency=max_concurrency,
        monitor_interval_s=0.2,
        pressure_check_interval_s=0.2,
        state_dir=state_dir,
        logs_dir=logs_dir,
    )
    pool = RunPool(config)
    items = [
        ProcessConfig(
            label=f"{step_id}-item-{i}",
            launch=PreparedLaunch(command=("echo", f"{step_id}-{i}")),
        )
        for i in range(n_items)
    ]
    await pool.submit_batch(items)
    await pool.shutdown()


@pytest.fixture
def composite_run(tmp_path: Path) -> Path:
    """Build a parent run dir with one composite child running two per-step pools."""
    parent = tmp_path / "dispatch-2026-05-19"
    _mark_v2(parent)

    child = parent / "analysis-research"
    _mark_v2(child)

    async def _drive_all() -> None:
        # Drive two per-step pools serially so the second one's pool_start
        # event lands cleanly after the first one's pool_shutdown. Doing
        # them in parallel works too but adds nondeterminism to the event
        # ordering assertion below.
        await _drive_step_pool(child, "research-step", n_items=3)
        await _drive_step_pool(child, "deep-research", n_items=2)

    asyncio.run(_drive_all())
    return parent


class TestPoolCommandsOverLiveCompositeRun:
    def test_status_renders_per_step_child_pools(self, composite_run: Path):
        result = runner.invoke(app, ["pool", "status", str(composite_run)])
        assert result.exit_code == 0, result.output
        # The composite parent itself has no run-level pool, but the
        # composite child (analysis-research) has per-step pools under
        # .state/steps/. pool status must discover and render each one.
        assert "Pool:" in result.output
        # Both step pools should appear.
        assert "research-step" in result.output
        assert "deep-research" in result.output

    def test_events_surfaces_pool_lifecycle_from_child_pools(self, composite_run: Path):
        result = runner.invoke(app, ["pool", "events", str(composite_run), "--json"])
        assert result.exit_code == 0, result.output
        events = json.loads(result.stdout)
        event_names = [event["event"] for event in events]
        # Two per-step pools were driven end-to-end — each emits a
        # pool_start, N process_start/exit pairs, and a pool_shutdown.
        assert event_names.count("pool_start") == 2
        assert event_names.count("pool_shutdown") == 2
        # 3 + 2 items across the two step pools.
        assert event_names.count("process_exit") == 5

    def test_events_summary_aggregates_across_composite_children(self, composite_run: Path):
        result = runner.invoke(app, ["pool", "events", str(composite_run), "--summary"])
        assert result.exit_code == 0, result.output
        assert "Events:" in result.output
        assert "pool_start" in result.output
        assert "process_exit" in result.output

    def test_health_finds_per_step_health_streams(self, composite_run: Path):
        result = runner.invoke(app, ["pool", "health", str(composite_run), "--summary"])
        assert result.exit_code == 0, result.output
        # Two pools, each writes at least one health sample. Don't pin
        # the exact count — the monitor cadence is wall-clock dependent.
        assert "Health samples:" in result.output

    def test_rollup_aggregates_completed_across_composite_children(self, composite_run: Path):
        result = runner.invoke(app, ["pool", "rollup", str(composite_run), "--json"])
        assert result.exit_code == 0, result.output
        rollup = json.loads(result.stdout)
        assert rollup["sub_pool_count"] == 2
        # 3 echo items + 2 echo items = 5 completed across the
        # composite child's two per-step pools.
        assert rollup["totals"]["completed"] == 5
        # And the per-step labels carry the composite child prefix so the
        # operator sees which composite child owns each row.
        step_dirs = sorted(entry["step_dir"] for entry in rollup["per_step"])
        assert step_dirs == [
            "analysis-research/deep-research",
            "analysis-research/research-step",
        ]

    def test_rollup_text_render_includes_relative_paths(self, composite_run: Path):
        result = runner.invoke(app, ["pool", "rollup", str(composite_run)])
        assert result.exit_code == 0, result.output
        assert "analysis-research/research-step" in result.output
        assert "analysis-research/deep-research" in result.output
        assert "Sub-pools: 2" in result.output
