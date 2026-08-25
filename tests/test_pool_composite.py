"""Composite-aware pool discovery — covers the fix.

Walks the shared discovery helper :func:`_iter_composite_run_dirs` plus the
four operator-facing commands (status, events, health, rollup) under a
parent run dir that has child run dirs at ``<parent>/<step_id>/``.

The fixture mimics a composite dispatch layout: a top-level run dir with no
runpool of its own and one composite child (``analysis-research``) that owns
two per-step pools.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from metaproc import paths as paths_mod
from metaproc.cli import app
from metaproc.commands.pool import (
    _collect_sub_pools,
    _composite_pool_status_files,
    _iter_composite_run_dirs,
    _runpool_event_files_for_read,
    _runpool_health_files_for_read,
)
from metaproc.io import to_yaml_string

runner = CliRunner()


def _mark_v2(run_dir: Path) -> None:
    paths_mod.run_state_dir(run_dir).mkdir(parents=True, exist_ok=True)
    paths_mod.run_config_file(run_dir).write_text(
        f"metaproc_layout: {paths_mod.RUN_LAYOUT_VERSION}\n", encoding="utf-8"
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_runpool_status(state_dir: Path, *, pool_id: str, completed_count: int = 0) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "pool_id": pool_id,
        "pid": os.getpid(),
        "started_at": "2026-05-19T00:00:00+00:00",
        "updated_at": "2026-05-19T00:00:01+00:00",
        "backend": "local",
        "max_concurrency": 8,
        "current_concurrency": 2,
        "active_count": 0,
        "pending_count": 0,
        "completed_count": completed_count,
        "failed_count": 0,
        "killed_count": 0,
        "pending_retries": 0,
        "pressure": {
            "level": "normal",
            "available_pct": 70.0,
            "swap_used_gb": 0.0,
            "total_memory_gb": 32.0,
            "source": "test",
        },
        "failure_counts": {
            "rate_limited": 0,
            "server_error": 0,
            "timeout": 0,
            "invalid_output": 0,
            "crash": 0,
            "unknown": 0,
        },
        "controller": {
            "mode": "adaptive",
            "operator_cap": 8,
            "effective_target": 8,
            "memory_ceiling": 8,
            "provider_ceiling": 8,
            "bottleneck": "uncapped",
            "recent_rate_limits": 0,
            "pending_retries": 0,
        },
        "processes": [],
        "recent_completions": [],
    }
    (state_dir / paths_mod.POOL_STATUS_FILE).write_text(to_yaml_string(payload))


@pytest.fixture
def composite_run(tmp_path: Path) -> Path:
    """Parent run dir with no top-level pool + one composite child running
    two per-step pools. Mirrors a composite dispatch fanning out to child runs."""
    parent = tmp_path / "dispatch-2026-05-19"
    _mark_v2(parent)

    child = parent / "analysis-research"
    _mark_v2(child)
    _write_runpool_status(
        paths_mod.step_state_dir(child, "research-step"),
        pool_id="pool-alt-data",
        completed_count=3,
    )
    _write_runpool_status(
        paths_mod.step_state_dir(child, "deep-research"),
        pool_id="pool-deep",
        completed_count=5,
    )
    _write_jsonl(
        paths_mod.runpool_step_events(child, "research-step"),
        [
            {
                "event": "pool_start",
                "ts": "2026-05-19T00:00:00+00:00",
                "backend": "local",
                "max_concurrency": 2,
            },
            {
                "event": "process_exit",
                "ts": "2026-05-19T00:00:05+00:00",
                "label": "ticker=CAVA",
                "pid": 100,
                "exit_code": 0,
            },
        ],
    )
    _write_jsonl(
        paths_mod.runpool_step_health(child, "research-step"),
        [
            {
                "event": "health_sample",
                "ts": "2026-05-19T00:00:00+00:00",
                "level": "normal",
                "memory_level": "normal",
                "available_pct": 65.0,
                "swap_used_gb": 0.0,
                "total_memory_gb": 32.0,
                "swap_delta_gb_per_min": 0.0,
                "swap_level": "normal",
                "disk_free_gb": 10.0,
                "disk_total_gb": 100.0,
                "disk_used_pct": 90.0,
                "disk_level": "normal",
                "disk_pressure_cause": "none",
                "current_concurrency": 2,
                "effective_target": 2,
                "active_count": 1,
            }
        ],
    )
    return parent


class TestIterCompositeRunDirs:
    def test_yields_root_first_then_nested_child(self, composite_run: Path):
        dirs = list(_iter_composite_run_dirs(composite_run))
        assert dirs[0] == composite_run
        assert composite_run / "analysis-research" in dirs

    def test_skips_state_and_logs_subtrees(self, composite_run: Path):
        dirs = list(_iter_composite_run_dirs(composite_run))
        for d in dirs:
            assert paths_mod.STATE_DIR not in d.parts[len(composite_run.parts) :]
            assert paths_mod.LOGS_DIR not in d.parts[len(composite_run.parts) :]

    def test_yields_only_root_when_no_nested_runs(self, tmp_path: Path):
        _mark_v2(tmp_path)
        assert list(_iter_composite_run_dirs(tmp_path)) == [tmp_path]

    def test_handles_missing_run_dir(self, tmp_path: Path):
        ghost = tmp_path / "nope"
        # No directory yet — helper should still yield the requested path
        # so the caller's downstream "no pool file" branch fires cleanly.
        assert list(_iter_composite_run_dirs(ghost)) == [ghost]

    def test_skips_worker_dirs_under_logs(self, composite_run: Path):
        # Worker dirs live under .logs/runpool/workers/ — they're already
        # filtered by the STATE/LOGS skip, but pin the guard against a
        # future layout that surfaces worker-* at the top level.
        (composite_run / "worker-0").mkdir()
        (composite_run / "worker-0" / paths_mod.STATE_DIR).mkdir()
        dirs = list(_iter_composite_run_dirs(composite_run))
        assert composite_run / "worker-0" not in dirs

    def test_follows_recursive_scope_layout_without_a_depth_limit(self, tmp_path: Path):
        _mark_v2(tmp_path)
        expected: list[Path] = []
        scope = tmp_path
        for index in range(6):
            scope = scope / f"step-{index}" / f"item-{index}"
            (scope / paths_mod.STATE_DIR).mkdir(parents=True)
            expected.append(scope)

        assert list(_iter_composite_run_dirs(tmp_path)) == [tmp_path, *expected]

    def test_does_not_follow_scope_symlink_outside_run_tree(self, tmp_path: Path):
        run_dir = tmp_path / "run"
        _mark_v2(run_dir)
        external_scope = tmp_path / "external" / "item"
        (external_scope / paths_mod.STATE_DIR).mkdir(parents=True)
        step_dir = run_dir / "step"
        step_dir.mkdir()
        (step_dir / "item").symlink_to(external_scope, target_is_directory=True)

        assert list(_iter_composite_run_dirs(run_dir)) == [run_dir]


class TestCompositePoolStatusFiles:
    def test_finds_child_pools_under_composite_parent(self, composite_run: Path):
        entries = _composite_pool_status_files(composite_run)
        # Parent itself has no runpool-status.yaml (composite parent doesn't
        # own a pool), but neither do the per-step children — the per-step
        # files live under .state/steps/ which pool_status doesn't walk.
        # _composite_pool_status_files only inspects each run dir's own
        # top-level runpool-status.yaml; the per-step files surface via
        # _collect_sub_pools / pool rollup.
        # Add a child-level runpool-status.yaml to verify the walker picks
        # it up at the composite-child run-dir level.
        child = composite_run / "analysis-research"
        _write_runpool_status(paths_mod.run_state_dir(child), pool_id="pool-analysis-research")
        entries = _composite_pool_status_files(composite_run)
        assert any(
            entry["run_dir"] == child and entry["status"].pool_id == "pool-analysis-research"
            for entry in entries
        )

    def test_discovers_per_step_pool_files_under_composite_child(self, composite_run: Path):
        """Pool status discovers per-step runpool-status.yaml files nested
        under composite child run dirs at .state/steps/<step>/.

        The composite_run fixture has two per-step pools under
        analysis-research/.state/steps/{research-step,deep-research}/
        but no run-level runpool-status.yaml. _composite_pool_status_files
        must walk into .state/steps/ to find them.
        """
        entries = _composite_pool_status_files(composite_run)
        pool_ids = sorted(entry["status"].pool_id for entry in entries if entry["status"])
        assert "pool-alt-data" in pool_ids
        assert "pool-deep" in pool_ids
        assert len(entries) >= 2

    def test_empty_when_no_pool_files_anywhere(self, tmp_path: Path):
        _mark_v2(tmp_path)
        # No runpool-status.yaml anywhere — bead-distinguished case
        # "no pool file at this level AND no composite child pools".
        assert _composite_pool_status_files(tmp_path) == []


class TestPoolStatusCommand:
    def test_discovers_per_step_pools_in_composite_run(self, composite_run: Path):
        """pool status on a composite parent with only per-step child pools
        must list each child pool — not report 'no pool file'."""
        result = runner.invoke(app, ["pool", "status", str(composite_run)])
        assert result.exit_code == 0, result.output
        assert "pool-alt-data" in result.output
        assert "pool-deep" in result.output

    def test_json_discovers_per_step_pools_in_composite_run(self, composite_run: Path):
        """pool status --json on a composite parent lists per-step child pools."""
        result = runner.invoke(app, ["pool", "status", str(composite_run), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        pool_ids = sorted(entry["status"]["pool_id"] for entry in data if entry["status"])
        assert "pool-alt-data" in pool_ids
        assert "pool-deep" in pool_ids

    def test_renders_child_pool_when_composite_child_has_run_level_pool(self, composite_run: Path):
        child = composite_run / "analysis-research"
        _write_runpool_status(paths_mod.run_state_dir(child), pool_id="pool-analysis-research")
        result = runner.invoke(app, ["pool", "status", str(composite_run)])
        assert result.exit_code == 0, result.output
        assert "[analysis-research]" in result.output
        assert "Pool: pool-analysis-research" in result.output

    def test_json_emits_one_entry_per_pool(self, composite_run: Path):
        child = composite_run / "analysis-research"
        _write_runpool_status(paths_mod.run_state_dir(child), pool_id="pool-analysis-research")
        result = runner.invoke(app, ["pool", "status", str(composite_run), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        # Run-level pool + 2 per-step pools under analysis-research.
        assert len(data) == 3
        pool_ids = sorted(entry["status"]["pool_id"] for entry in data if entry["status"])
        assert "pool-analysis-research" in pool_ids
        assert "pool-alt-data" in pool_ids
        assert "pool-deep" in pool_ids


class TestPoolEventsCommand:
    def test_pool_events_discovers_child_run_events(self, composite_run: Path):
        # Parent run has no events of its own, but the composite child
        # writes V2 step-scoped events. Previously, the parent-rooted
        # invocation reported "No RunPool event files".
        result = runner.invoke(app, ["pool", "events", str(composite_run), "--json"])
        assert result.exit_code == 0, result.output
        events = json.loads(result.stdout)
        names = [event["event"] for event in events]
        assert "pool_start" in names
        assert "process_exit" in names


class TestPoolHealthCommand:
    def test_pool_health_discovers_child_run_health(self, composite_run: Path):
        result = runner.invoke(app, ["pool", "health", str(composite_run), "--summary"])
        assert result.exit_code == 0, result.output
        assert "Health samples: 1" in result.output


class TestPoolRollupCommand:
    def test_rollup_walks_composite_child_per_step_pools(self, composite_run: Path):
        sub_pools = _collect_sub_pools(composite_run, with_auth_outcomes=False)
        # Two per-step pools nested under <parent>/analysis-research/.state/steps/
        # — previously, _collect_sub_pools only walked
        # <parent>/.state/steps/ and found nothing.
        step_dirs = sorted(entry["step_dir"] for entry in sub_pools)
        assert step_dirs == [
            "analysis-research/deep-research",
            "analysis-research/research-step",
        ]

    def test_rollup_command_aggregates_across_composite_children(self, composite_run: Path):
        result = runner.invoke(app, ["pool", "rollup", str(composite_run), "--json"])
        assert result.exit_code == 0, result.output
        rollup = json.loads(result.stdout)
        assert rollup["sub_pool_count"] == 2
        # 3 + 5 across the two per-step pools.
        assert rollup["totals"]["completed"] == 8

    def test_rollup_renders_relative_paths_for_composite_children(self, composite_run: Path):
        result = runner.invoke(app, ["pool", "rollup", str(composite_run)])
        assert result.exit_code == 0, result.output
        # Composite child path component leaks into the per-step label so
        # operators see *which* composite child owns each pool.
        assert "analysis-research/research-step" in result.output
        assert "analysis-research/deep-research" in result.output

    def test_rollup_empty_run_dir_surfaces_message(self, tmp_path: Path):
        _mark_v2(tmp_path)
        result = runner.invoke(app, ["pool", "rollup", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No pool dirs found" in result.output


class TestRunPoolEventAndHealthHelpers:
    """Pin the lower-level helpers used by the rendered commands."""

    def test_event_files_dedup_across_composite_children(self, composite_run: Path):
        files = _runpool_event_files_for_read(composite_run)
        # Every returned path should be unique even though the walker
        # visits both the parent run and the child run.
        assert len(files) == len(set(files))
        assert any("research-step" in str(p) for p in files)

    def test_health_files_dedup_across_composite_children(self, composite_run: Path):
        files = _runpool_health_files_for_read(composite_run)
        assert len(files) == len(set(files))
        assert any("research-step" in str(p) for p in files)
