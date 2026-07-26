"""CLI coverage for RunPool event readers across V2 scoped streams."""

from __future__ import annotations

import json
import os
from pathlib import Path

import psutil
from typer.testing import CliRunner

from metaproc import paths as paths_mod
from metaproc.cli import app
from metaproc.runpool.status import (
    ControllerStatus,
    ScaleOverride,
    ScaleState,
    read_scale_override,
    write_scale_override,
    write_scale_state,
)

runner = CliRunner()


def _mark_v2(run_dir: Path) -> None:
    paths_mod.run_state_dir(run_dir).mkdir(parents=True)
    paths_mod.run_config_file(run_dir).write_text(
        f"metaproc_layout: {paths_mod.RUN_LAYOUT_VERSION}\n", encoding="utf-8"
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _concurrency_plan() -> dict[str, object]:
    return {
        "schema_id": "metaproc.runpool.concurrency-plan/v1",
        "backend": "local",
        "execution_profile": "codex-gpt55",
        "cli_max_concurrency": 10,
        "batch_size": 20,
        "requested_max_concurrency": 10,
        "profile_max_concurrency_hint": 8,
        "host_max_concurrency": 6,
        "configured_max_concurrency": 8,
        "operator_cap": None,
        "initial_concurrency_override": None,
        "initial_concurrency_estimate": 2,
        "initial_concurrency": 2,
        "min_concurrency": 1,
        "estimated_process_rss_bytes": 4 * 1024 * 1024 * 1024,
        "initial_memory_budget_fraction": 0.25,
        "initial_available_memory_bytes": 32 * 1024 * 1024 * 1024,
        "initial_total_memory_bytes": 64 * 1024 * 1024 * 1024,
        "limiting_factor": "memory_estimate",
    }


def _write_runpool_status(run_dir: Path, *, concurrency_plan: dict[str, object]) -> None:
    status_path = paths_mod.run_state_dir(run_dir) / paths_mod.POOL_STATUS_FILE
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps(
            {
                "pool_id": "pool-test",
                "pid": os.getpid(),
                "started_at": "2026-05-19T00:00:00+00:00",
                "updated_at": "2026-05-19T00:00:01+00:00",
                "backend": "local",
                "max_concurrency": 8,
                "current_concurrency": 2,
                "active_count": 0,
                "pending_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "killed_count": 0,
                "pressure": {
                    "level": "normal",
                    "available_pct": 50.0,
                    "swap_used_gb": 0.0,
                    "total_memory_gb": 64.0,
                    "source": "test",
                },
                "concurrency_plan": concurrency_plan,
            }
        ),
        encoding="utf-8",
    )


def test_pool_status_renders_concurrency_plan(tmp_path: Path) -> None:
    _write_runpool_status(tmp_path, concurrency_plan=_concurrency_plan())

    result = runner.invoke(app, ["pool", "status", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Plan: profile=codex-gpt55 backend=local requested=10 configured=8" in result.output
    assert "limit=memory_estimate host=6 rss=4096MB budget=0.25" in result.output


def test_pool_events_reads_v2_scoped_streams(tmp_path: Path) -> None:
    _mark_v2(tmp_path)
    _write_jsonl(
        paths_mod.runpool_step_events(tmp_path, "fanout"),
        [
            {
                "event": "pool_start",
                "ts": "2026-05-14T00:00:00+00:00",
                "backend": "local",
                "max_concurrency": 2,
            },
            {
                "event": "process_start",
                "ts": "2026-05-14T00:00:01+00:00",
                "label": "ticker=AAPL",
                "pid": 123,
            },
        ],
    )
    _write_jsonl(
        paths_mod.runpool_worker_events(tmp_path, "0"),
        [
            {
                "event": "process_exit",
                "ts": "2026-05-14T00:00:02+00:00",
                "label": "ticker=AAPL",
                "pid": 123,
                "exit_code": 0,
            },
        ],
    )

    result = runner.invoke(app, ["pool", "events", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    events = json.loads(result.stdout)
    assert [event["event"] for event in events] == [
        "pool_start",
        "process_start",
        "process_exit",
    ]
    assert events[1]["label"] == "ticker=AAPL"


def test_pool_events_renders_concurrency_plan_on_pool_start(tmp_path: Path) -> None:
    _mark_v2(tmp_path)
    _write_jsonl(
        paths_mod.runpool_step_events(tmp_path, "fanout"),
        [
            {
                "event": "pool_start",
                "ts": "2026-05-14T00:00:00+00:00",
                "backend": "local",
                "max_concurrency": 8,
                "initial_concurrency": 2,
                "concurrency_plan": _concurrency_plan(),
            }
        ],
    )

    result = runner.invoke(app, ["pool", "events", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "pool_start  backend=local max=8 initial=2" in result.output
    assert "plan=(profile=codex-gpt55 backend=local requested=10 configured=8" in result.output


def test_pool_health_reads_v2_scoped_streams(tmp_path: Path) -> None:
    _mark_v2(tmp_path)
    _write_jsonl(
        paths_mod.runpool_step_health(tmp_path, "fanout"),
        [
            {
                "event": "health_sample",
                "ts": "2026-05-14T00:00:00+00:00",
                "level": "high",
                "memory_level": "normal",
                "available_pct": 65.0,
                "swap_used_gb": 40.0,
                "total_memory_gb": 32.0,
                "swap_delta_gb_per_min": 0.0,
                "swap_level": "normal",
                "disk_free_gb": 6.0,
                "disk_total_gb": 460.0,
                "disk_used_pct": 98.7,
                "disk_level": "high",
                "disk_pressure_cause": "swap_reserve_high",
                "current_concurrency": 3,
                "effective_target": 3,
                "active_count": 2,
            }
        ],
    )

    result = runner.invoke(app, ["pool", "health", str(tmp_path), "--summary"])

    assert result.exit_code == 0, result.output
    assert "Health samples: 1" in result.output
    assert "Memory levels: {'normal': 1}" in result.output
    assert "Swap levels: {'normal': 1}" in result.output
    assert "swap_reserve_high" in result.output


def test_pool_host_slots_lists_disk_backed_leases(tmp_path: Path) -> None:
    slot_dir = tmp_path / "local-agents" / "slot-0"
    slot_dir.mkdir(parents=True)
    current_pid = os.getpid()
    (slot_dir / "lease.json").write_text(
        json.dumps(
            {
                "schema": "metaproc.runpool.host-admission/v1",
                "namespace": "local-agents",
                "slot_id": 0,
                "limit": 8,
                "label": "ticker=TEST",
                "pool_id": "pool-test",
                "owner_pid": current_pid,
                "owner_create_time": psutil.Process(current_pid).create_time(),
                "child_pid": None,
                "child_create_time": None,
                "acquired_at": "2026-05-18T00:00:00Z",
                "updated_at": "2026-05-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["pool", "host-slots", "--root-dir", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0]["slot_id"] == 0
    assert data[0]["label"] == "ticker=TEST"
    assert data[0]["status"] == "alive"
    assert data[0]["owner_alive"] is True


def test_pool_events_summarizes_pressure_swap_levels(tmp_path: Path) -> None:
    _mark_v2(tmp_path)
    _write_jsonl(
        paths_mod.runpool_step_events(tmp_path, "fanout"),
        [
            {
                "event": "pressure_check",
                "ts": "2026-05-14T00:00:00+00:00",
                "level": "normal",
                "memory_level": "normal",
                "available_pct": 62.0,
                "swap_used_gb": 3.0,
                "total_memory_gb": 64.0,
                "swap_delta_gb_per_min": 0.0,
                "swap_level": "normal",
                "current_concurrency": 5,
                "effective_target": 8,
                "active_count": 4,
            },
            {
                "event": "pressure_check",
                "ts": "2026-05-14T00:00:10+00:00",
                "level": "high",
                "memory_level": "elevated",
                "available_pct": 34.0,
                "swap_used_gb": 5.0,
                "total_memory_gb": 64.0,
                "swap_delta_gb_per_min": 2.0,
                "swap_level": "high",
                "current_concurrency": 4,
                "effective_target": 6,
                "active_count": 4,
            },
        ],
    )

    result = runner.invoke(
        app,
        ["pool", "events", str(tmp_path), "--type", "pressure_check", "--summary"],
    )

    assert result.exit_code == 0, result.output
    assert "Swap levels: {'normal': 1, 'high': 1}" in result.output
    assert "delta_max=2.0GB/min" in result.output
    assert "growth_samples=1" in result.output


def test_pool_concurrency_timeline_reads_v2_step_scoped_stream(tmp_path: Path) -> None:
    _mark_v2(tmp_path)
    _write_jsonl(
        paths_mod.runpool_step_events(tmp_path, "fanout"),
        [
            {
                "event": "pool_start",
                "ts": "2026-05-14T00:00:00+00:00",
                "backend": "local",
                "max_concurrency": 8,
                "initial_concurrency": 2,
                "concurrency_plan": _concurrency_plan(),
            },
            {
                "event": "concurrency_adjust",
                "ts": "2026-05-14T00:01:00+00:00",
                "old": 2,
                "new": 4,
                "reason": "pressure_normal",
            },
        ],
    )

    result = runner.invoke(app, ["pool", "concurrency-timeline", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    timeline = json.loads(result.stdout)
    assert timeline["pool_start"]["max_concurrency"] == 8
    assert timeline["pool_start"]["concurrency_plan"]["limiting_factor"] == "memory_estimate"
    assert timeline["adjustments_total"] == 1
    assert timeline["adjustments"][0]["new"] == 4


def test_pool_override_writes_run_level_override_without_active_pools(tmp_path: Path) -> None:
    result = runner.invoke(app, ["pool", "override", str(tmp_path), "--cap", "4"])

    assert result.exit_code == 0, result.output
    override = read_scale_override(
        paths_mod.run_state_dir(tmp_path) / paths_mod.SCALE_OVERRIDE_FILE
    )
    assert override.operator_cap == 4
    assert "future pools" in result.output


def test_pool_override_writes_run_level_and_active_step_overrides(tmp_path: Path) -> None:
    step_state_dir = paths_mod.step_state_dir(tmp_path, "fanout")
    write_scale_state(
        step_state_dir / paths_mod.SCALE_STATE_FILE,
        ScaleState(
            updated_at="2026-05-14T00:00:00Z",
            controller=ControllerStatus(
                operator_cap=8,
                effective_target=8,
                memory_ceiling=8,
                provider_ceiling=8,
                bottleneck="uncapped",
            ),
        ),
    )

    result = runner.invoke(app, ["pool", "override", str(tmp_path), "--cap", "5"])

    assert result.exit_code == 0, result.output
    run_override = read_scale_override(
        paths_mod.run_state_dir(tmp_path) / paths_mod.SCALE_OVERRIDE_FILE
    )
    step_override = read_scale_override(step_state_dir / paths_mod.SCALE_OVERRIDE_FILE)
    assert run_override.operator_cap == 5
    assert step_override.operator_cap == 5


def test_pool_override_clear_removes_run_level_and_active_step_overrides(
    tmp_path: Path,
) -> None:
    step_state_dir = paths_mod.step_state_dir(tmp_path, "fanout")
    write_scale_state(
        step_state_dir / paths_mod.SCALE_STATE_FILE,
        ScaleState(
            updated_at="2026-05-14T00:00:00Z",
            controller=ControllerStatus(
                operator_cap=4,
                effective_target=4,
                memory_ceiling=8,
                provider_ceiling=8,
                bottleneck="operator-capped",
            ),
        ),
    )
    run_override_path = paths_mod.run_state_dir(tmp_path) / paths_mod.SCALE_OVERRIDE_FILE
    step_override_path = step_state_dir / paths_mod.SCALE_OVERRIDE_FILE
    write_scale_override(run_override_path, ScaleOverride(mode="adaptive", operator_cap=4))
    write_scale_override(step_override_path, ScaleOverride(mode="adaptive", operator_cap=4))

    result = runner.invoke(app, ["pool", "override", str(tmp_path), "--clear"])

    assert result.exit_code == 0, result.output
    assert not run_override_path.exists()
    assert not step_override_path.exists()
    assert "Removed 2 override file(s)" in result.output


def test_pool_override_writes_nested_run_state_overrides_for_future_pools(
    tmp_path: Path,
) -> None:
    nested_run_dir = tmp_path / "analysis-research"
    nested_state_dir = paths_mod.run_state_dir(nested_run_dir)
    nested_state_dir.mkdir(parents=True)

    result = runner.invoke(app, ["pool", "override", str(tmp_path), "--cap", "3"])

    assert result.exit_code == 0, result.output
    root_override = read_scale_override(
        paths_mod.run_state_dir(tmp_path) / paths_mod.SCALE_OVERRIDE_FILE
    )
    nested_override = read_scale_override(nested_state_dir / paths_mod.SCALE_OVERRIDE_FILE)
    assert root_override.operator_cap == 3
    assert nested_override.operator_cap == 3
