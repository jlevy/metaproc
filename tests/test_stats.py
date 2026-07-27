"""Tests for metaproc.stats — event models, reader, analysis, and engine."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from metaproc.engine.run_status import TimingStats
from metaproc.osutils.memory_pressure import PressureLevel
from metaproc.paths import runpool_worker_events, step_state_dir, worker_state_dir
from metaproc.runpool.concurrency import ConcurrencyPlan
from metaproc.runpool.event_models import (
    ConcurrencyAdjustEvent,
    PoolStartEvent,
    ProcessExitEvent,
    ProcessStartEvent,
    RunPoolEvent,
)
from metaproc.runpool.event_reader import read_runpool_events
from metaproc.runpool.status import (
    ControllerStatus,
    FailureCounts,
    PressureStatus,
    RunPoolStatus,
    ScaleBounds,
    ScaleState,
    write_scale_state,
    write_status,
)
from metaproc.stats.analysis import analyze_runpool_events
from metaproc.stats.engine import _compute_api_stats, _compute_pool_stats
from metaproc.stats.formatters import format_full
from metaproc.stats.models import (
    PoolFinding,
    PoolStats,
    ProgressStats,
    ResourceStats,
    RunStats,
    ThroughputStats,
    VariantProgress,
)


def test_compute_api_stats_reads_v2_task_log_layout(tmp_path: Path) -> None:
    log_dir = tmp_path / ".logs" / "tasks" / "analyze" / "item-1"
    log_dir.mkdir(parents=True)
    records = [
        {"type": "system", "subtype": "init", "model": "synthetic-model"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "input.md"}}
                ]
            },
        },
        {"type": "result", "is_error": False, "cost_usd": 0.25},
    ]
    (log_dir / "attempt.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    stats = _compute_api_stats(tmp_path)

    assert stats is not None
    assert stats.total_calls == 1
    assert stats.models == {"synthetic-model": 1}
    assert stats.total_cost_usd == 0.25


# ── Event model fixtures ────────────────────────────────────────

SAMPLE_EVENTS_RAW = [
    {
        "event": "pool_start",
        "pool_id": "pool-1",
        "backend": "local",
        "max_concurrency": 30,
        "initial_concurrency": 20,
        "ts": "2026-04-10T07:00:00+00:00",
    },
    {
        "event": "process_start",
        "pid": 100,
        "backend": "local",
        "label": "ITEM=AAPL",
        "ts": "2026-04-10T07:00:01+00:00",
    },
    {
        "event": "process_start",
        "pid": 101,
        "backend": "local",
        "label": "ITEM=MSFT",
        "ts": "2026-04-10T07:00:02+00:00",
    },
    {
        "event": "pressure_check",
        "level": "normal",
        "available_pct": 95.0,
        "ts": "2026-04-10T07:00:05+00:00",
    },
    {
        "event": "process_exit",
        "pid": 100,
        "backend": "local",
        "label": "ITEM=AAPL",
        "exit_code": 0,
        "elapsed_s": 120.5,
        "peak_rss_bytes": 50_000_000,
        "ts": "2026-04-10T07:02:01+00:00",
    },
    {
        "event": "process_exit",
        "pid": 101,
        "backend": "local",
        "label": "ITEM=MSFT",
        "exit_code": 1,
        "elapsed_s": 85.0,
        "peak_rss_bytes": 45_000_000,
        "failure_class": "invalid_output",
        "ts": "2026-04-10T07:01:27+00:00",
    },
    {
        "event": "concurrency_adjust",
        "old": 20,
        "new": 25,
        "reason": "ramp_up",
        "ts": "2026-04-10T07:02:30+00:00",
    },
    {
        "event": "process_start",
        "pid": 102,
        "backend": "local",
        "label": "ITEM=MSFT",
        "ts": "2026-04-10T07:02:31+00:00",
    },
    {
        "event": "process_exit",
        "pid": 102,
        "backend": "local",
        "label": "ITEM=MSFT",
        "exit_code": 0,
        "elapsed_s": 200.0,
        "peak_rss_bytes": 60_000_000,
        "ts": "2026-04-10T07:05:51+00:00",
    },
    {
        "event": "pool_shutdown",
        "pool_id": "pool-1",
        "completed": 2,
        "failed": 1,
        "killed": 0,
        "ts": "2026-04-10T07:06:00+00:00",
    },
]


def _write_events(events: list[dict[str, Any]]) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for ev in events:
        f.write(json.dumps(ev) + "\n")
    f.close()
    return Path(f.name)


# ── Event model tests ───────────────────────────────────────────


class TestEventModels:
    def test_discriminated_union_pool_start(self) -> None:
        adapter = TypeAdapter(RunPoolEvent)
        ev = adapter.validate_python(SAMPLE_EVENTS_RAW[0])
        assert isinstance(ev, PoolStartEvent)
        assert ev.pool_id == "pool-1"
        assert ev.max_concurrency == 30

    def test_discriminated_union_process_exit(self) -> None:
        adapter = TypeAdapter(RunPoolEvent)
        ev = adapter.validate_python(SAMPLE_EVENTS_RAW[4])
        assert isinstance(ev, ProcessExitEvent)
        assert ev.exit_code == 0
        assert ev.elapsed_s == 120.5
        assert ev.peak_rss_bytes == 50_000_000

    def test_discriminated_union_concurrency_adjust(self) -> None:
        adapter = TypeAdapter(RunPoolEvent)
        ev = adapter.validate_python(SAMPLE_EVENTS_RAW[6])
        assert isinstance(ev, ConcurrencyAdjustEvent)
        assert ev.old == 20
        assert ev.new == 25

    def test_all_event_types_parse(self) -> None:
        adapter = TypeAdapter(RunPoolEvent)
        for raw in SAMPLE_EVENTS_RAW:
            ev = adapter.validate_python(raw)
            assert ev.ts is not None


# ── Event reader tests ──────────────────────────────────────────


class TestEventReader:
    def test_reads_all_events(self) -> None:
        path = _write_events(SAMPLE_EVENTS_RAW)
        events = read_runpool_events(path)
        assert len(events) == len(SAMPLE_EVENTS_RAW)

    def test_skips_malformed_lines(self) -> None:
        path = _write_events(SAMPLE_EVENTS_RAW)
        # Append bad lines
        with path.open("a") as f:
            f.write("not json\n")
            f.write('{"no_event_field": true}\n')
            f.write("\n")
        events = read_runpool_events(path)
        assert len(events) == len(SAMPLE_EVENTS_RAW)

    def test_empty_file(self) -> None:
        path = _write_events([])
        events = read_runpool_events(path)
        assert events == []

    def test_returns_typed_events(self) -> None:
        path = _write_events(SAMPLE_EVENTS_RAW)
        events = read_runpool_events(path)
        assert isinstance(events[0], PoolStartEvent)
        assert isinstance(events[1], ProcessStartEvent)


# ── Analysis tests ──────────────────────────────────────────────


class TestAnalysis:
    def test_taxonomy_counts(self) -> None:
        path = _write_events(SAMPLE_EVENTS_RAW)
        events = read_runpool_events(path)
        analysis = analyze_runpool_events(events)

        assert analysis.taxonomy_counts["process/start"] == 3
        assert analysis.taxonomy_counts["process/exit/success"] == 2
        assert analysis.taxonomy_counts["process/exit/failed"] == 1
        assert analysis.taxonomy_counts["pressure/normal"] == 1
        assert analysis.taxonomy_counts["concurrency/adjust"] == 1

    def test_aggregate_stats(self) -> None:
        path = _write_events(SAMPLE_EVENTS_RAW)
        events = read_runpool_events(path)
        analysis = analyze_runpool_events(events)

        assert analysis.total_process_runs == 3
        assert analysis.successful_exits == 2
        assert analysis.failed_exits == 1
        assert len(analysis.exit_durations_s) == 3
        assert analysis.concurrency_adjustments == 1

    def test_failure_counts(self) -> None:
        path = _write_events(SAMPLE_EVENTS_RAW)
        events = read_runpool_events(path)
        analysis = analyze_runpool_events(events)

        assert analysis.failure_counts.invalid_output == 1
        assert analysis.failure_counts.rate_limited == 0
        assert analysis.failure_counts.crash == 0

    def test_metadata(self) -> None:
        path = _write_events(SAMPLE_EVENTS_RAW)
        events = read_runpool_events(path)
        analysis = analyze_runpool_events(events)

        assert analysis.metadata["pool_id"] == "pool-1"
        assert analysis.metadata["backend"] == "local"
        assert analysis.metadata["max_concurrency"] == 30
        assert analysis.metadata["completed"] == 2

    def test_time_series(self) -> None:
        path = _write_events(SAMPLE_EVENTS_RAW)
        events = read_runpool_events(path)
        analysis = analyze_runpool_events(events)

        assert len(analysis.pressure_series) == 1
        assert analysis.pressure_series[0].y == 95.0
        # 3 starts + 3 exits = 6 running count points
        assert len(analysis.running_count_series) == 6
        # pool_start cap + concurrency_adjust = 2 cap points
        assert len(analysis.concurrency_cap_series) == 2

    def test_peak_rss_values(self) -> None:
        path = _write_events(SAMPLE_EVENTS_RAW)
        events = read_runpool_events(path)
        analysis = analyze_runpool_events(events)

        assert len(analysis.peak_rss_values) == 3
        assert 50_000_000 in analysis.peak_rss_values

    def test_empty_events(self) -> None:
        analysis = analyze_runpool_events([])
        assert analysis.total_process_runs == 0
        assert analysis.taxonomy_counts == {}


# ── RunStats model tests ────────────────────────────────────────


class TestRunStatsModel:
    def test_serialization_round_trip(self) -> None:
        stats = RunStats(
            run_dir="/tmp/test-run",
            computed_at=datetime(2026, 4, 10, 12, 0, 0),
        )
        data = stats.model_dump(mode="json")
        assert data["run_dir"] == "/tmp/test-run"
        assert data["progress"] is None
        assert data["throughput"] is None

        # Round-trip
        restored = RunStats.model_validate(data)
        assert restored.run_dir == "/tmp/test-run"

    def test_full_model_serialization(self) -> None:

        stats = RunStats(
            run_dir="/tmp/test-run",
            computed_at=datetime(2026, 4, 10, 12, 0, 0),
            progress=ProgressStats(
                total_items=100,
                completed=80,
                failed=5,
                running=10,
                pending=5,
                variants={
                    "deepseek": VariantProgress(
                        completed=80,
                        running=10,
                        failed=5,
                        pending=5,
                        total=100,
                        pct=80.0,
                        timing=TimingStats(
                            avg_seconds=300.0,
                            min_seconds=60.0,
                            max_seconds=900.0,
                            p50_seconds=280.0,
                            p95_seconds=800.0,
                            p99_seconds=880.0,
                        ),
                    )
                },
                wall_time_s=3600.0,
            ),
            throughput=ThroughputStats(
                items_per_hour=80.0,
                recent_items_per_hour=90.0,
                unique_completed=80,
                total_process_runs=150,
                retry_factor=1.9,
                retry_pct=46.7,
                hourly_breakdown=[],
            ),
            pool=PoolStats(
                num_workers=2,
                worker_ids=["worker-0", "worker-1"],
                desired_workers=3,
                current_concurrency=28,
                max_concurrency=30,
                desired_max_concurrency=15,
                scale_generation=2,
                live_worker_caps={"worker-0": 15, "worker-1": 15},
                concurrency_adjustments=3,
                process_starts=150,
                process_successful_exits=145,
                process_failed_exits=5,
                process_failure_counts=FailureCounts(invalid_output=5),
                runner_failure_counts=FailureCounts(rate_limited=12),
            ),
            resources=ResourceStats(
                avg_peak_rss_bytes=50_000_000,
                max_peak_rss_bytes=80_000_000,
                swap_used_gb=0.0,
                kills_by_reason={},
            ),
        )

        data = stats.model_dump(mode="json")
        assert data["progress"]["total_items"] == 100
        assert data["throughput"]["retry_factor"] == 1.9
        assert data["pool"]["process_failure_counts"]["invalid_output"] == 5
        assert data["pool"]["runner_failure_counts"]["rate_limited"] == 12
        assert data["pool"]["process_starts"] == 150
        assert data["pool"]["desired_workers"] == 3
        assert data["pool"]["desired_max_concurrency"] == 15
        assert data["resources"]["avg_peak_rss_bytes"] == 50_000_000


class TestPoolStatsEngine:
    def test_compute_pool_stats_uses_live_worker_totals_without_double_counting(
        self, tmp_path: Path
    ):
        events_path = _write_events(SAMPLE_EVENTS_RAW)
        analysis = analyze_runpool_events(read_runpool_events(events_path))

        run_dir = tmp_path / "run"
        for worker_id in ("worker-0", "worker-1"):
            worker_events = runpool_worker_events(run_dir, worker_id)
            worker_events.parent.mkdir(parents=True, exist_ok=True)
            worker_events.write_text("")

        status_a = RunPoolStatus(
            pool_id="pool-a",
            pid=111,
            started_at="2026-04-10T07:00:00+00:00",
            updated_at="2026-04-10T07:10:00+00:00",
            backend="local",
            max_concurrency=10,
            current_concurrency=6,
            active_count=3,
            pending_count=0,
            completed_count=10,
            failed_count=0,
            killed_count=0,
            pressure=PressureStatus(
                level=PressureLevel.NORMAL,
                available_pct=90.0,
                swap_used_gb=0.0,
                total_memory_gb=32.0,
                source="test",
            ),
            failure_counts=FailureCounts(rate_limited=2),
            controller=ControllerStatus(
                mode="adaptive",
                operator_cap=10,
                effective_target=6,
                memory_ceiling=10,
                provider_ceiling=6,
                bottleneck="DSQ-bound",
                recent_rate_limits=1,
                pending_retries=0,
            ),
        )
        status_b = RunPoolStatus(
            pool_id="pool-b",
            pid=222,
            started_at="2026-04-10T07:00:00+00:00",
            updated_at="2026-04-10T07:10:00+00:00",
            backend="local",
            max_concurrency=12,
            current_concurrency=5,
            active_count=2,
            pending_count=0,
            completed_count=8,
            failed_count=0,
            killed_count=0,
            pressure=PressureStatus(
                level=PressureLevel.ELEVATED,
                available_pct=70.0,
                swap_used_gb=0.0,
                total_memory_gb=32.0,
                source="test",
            ),
            failure_counts=FailureCounts(rate_limited=3, invalid_output=1),
            controller=ControllerStatus(
                mode="adaptive",
                operator_cap=12,
                effective_target=5,
                memory_ceiling=12,
                provider_ceiling=5,
                bottleneck="DSQ-bound",
                recent_rate_limits=2,
                pending_retries=1,
            ),
        )

        write_status(worker_state_dir(run_dir, "worker-0") / "runpool-status.yaml", status_a)
        write_status(worker_state_dir(run_dir, "worker-1") / "runpool-status.yaml", status_b)

        assert status_b.controller is not None
        write_scale_state(
            step_state_dir(run_dir, "generate-record") / "scale-state.yaml",
            ScaleState(
                updated_at="2026-04-10T07:11:00+00:00",
                controller=status_b.controller,
                desired_workers=3,
                desired_max_concurrency=15,
                generation=2,
                bounds=ScaleBounds(
                    min_workers=1,
                    max_workers=4,
                    min_concurrency=1,
                    max_concurrency=20,
                ),
            ),
        )

        stats = _compute_pool_stats(analysis, run_dir)

        assert stats.num_workers == 2
        assert stats.desired_workers == 3
        assert stats.current_concurrency == 11
        assert stats.max_concurrency == 22
        assert stats.desired_max_concurrency == 15
        assert stats.scale_generation == 2
        assert stats.live_worker_caps == {"worker-0": 10, "worker-1": 12}
        assert stats.provider_ceiling == 11
        assert stats.memory_ceiling == 22
        assert stats.effective_target == 11
        assert stats.recent_rate_limits == 3
        assert stats.bottleneck == "DSQ-bound"
        assert stats.pressure is not None
        assert stats.pressure.level.value == "elevated"
        assert stats.runner_failure_counts.rate_limited == 5
        assert stats.runner_failure_counts.invalid_output == 1
        assert stats.findings[0].code == "desired_workers_unmet"

    def test_compute_pool_stats_surfaces_concurrency_adequacy_findings(self, tmp_path: Path):

        analysis = analyze_runpool_events([])
        run_dir = tmp_path / "run"
        status = RunPoolStatus(
            pool_id="pool-local",
            pid=111,
            started_at="2026-04-10T07:00:00+00:00",
            updated_at="2026-04-10T07:10:00+00:00",
            backend="local",
            max_concurrency=5,
            current_concurrency=5,
            active_count=2,
            pending_count=6,
            completed_count=10,
            failed_count=1,
            killed_count=0,
            pressure=PressureStatus(
                level=PressureLevel.NORMAL,
                available_pct=82.0,
                swap_used_gb=0.0,
                total_memory_gb=64.0,
                source="test",
            ),
            controller=ControllerStatus(
                mode="adaptive",
                operator_cap=5,
                effective_target=5,
                memory_ceiling=5,
                provider_ceiling=5,
                bottleneck="uncapped",
                recent_rate_limits=0,
                pending_retries=0,
            ),
            concurrency_plan=ConcurrencyPlan(
                backend="local",
                execution_profile="codex-gpt55",
                cli_max_concurrency=10,
                batch_size=10,
                requested_max_concurrency=10,
                profile_max_concurrency_hint=30,
                host_max_concurrency=5,
                configured_max_concurrency=5,
                operator_cap=None,
                initial_concurrency_override=None,
                initial_concurrency_estimate=2,
                initial_concurrency=2,
                min_concurrency=1,
                estimated_process_rss_bytes=2_000_000_000,
                initial_memory_budget_fraction=0.25,
                initial_available_memory_bytes=20_000_000_000,
                initial_total_memory_bytes=64_000_000_000,
                limiting_factor="host_cap",
            ),
        )
        write_status(step_state_dir(run_dir, "generate-record") / "runpool-status.yaml", status)

        stats = _compute_pool_stats(analysis, run_dir)

        assert stats.active_count == 2
        assert stats.pending_count == 6
        codes = {finding.code for finding in stats.findings}
        assert "pending_queue_below_target" in codes
        assert "configured_concurrency_below_requested" in codes
        assert "host_concurrency_below_requested" in codes
        assert "initial_concurrency_below_configured" in codes


# ── Formatter tests ─────────────────────────────────────────────


class TestFormatFull:
    def test_empty_stats(self) -> None:
        stats = RunStats(
            run_dir="/tmp/test-run",
            computed_at=datetime(2026, 4, 10, 12, 0, 0),
        )
        output = format_full(stats)
        assert output == ""

    def test_progress_section(self) -> None:

        stats = RunStats(
            run_dir="/tmp/test-run",
            computed_at=datetime(2026, 4, 10, 12, 0, 0),
            progress=ProgressStats(
                total_items=100,
                completed=80,
                failed=5,
                running=10,
                pending=5,
                variants={
                    "deepseek": VariantProgress(
                        completed=80,
                        running=10,
                        failed=5,
                        pending=5,
                        total=100,
                        pct=80.0,
                    )
                },
                wall_time_s=3600.0,
            ),
        )
        output = format_full(stats)
        assert "Run: /tmp/test-run" in output
        assert "RUNNING" in output
        assert "deepseek" in output
        assert "80" in output

    def test_pool_section(self) -> None:

        stats = RunStats(
            run_dir="/tmp/test-run",
            computed_at=datetime(2026, 4, 10, 12, 0, 0),
            pool=PoolStats(
                num_workers=2,
                worker_ids=["worker-0", "worker-1"],
                desired_workers=3,
                active_count=24,
                pending_count=4,
                completed_count=90,
                failed_count=2,
                current_concurrency=28,
                max_concurrency=30,
                desired_max_concurrency=15,
                scale_generation=2,
                live_worker_caps={"worker-0": 15, "worker-1": 15},
                bottleneck="DSQ-bound",
                provider_ceiling=28,
                memory_ceiling=40,
                operator_cap=30,
                effective_target=28,
                recent_rate_limits=4,
                concurrency_adjustments=3,
                findings=[
                    PoolFinding(
                        severity="warning",
                        code="pending_queue_below_target",
                        message="4 items are pending while only 24/28 target slots are active.",
                    )
                ],
                process_starts=120,
                process_successful_exits=115,
                process_failed_exits=5,
                process_failure_counts=FailureCounts(timeout=3, crash=2),
                runner_failure_counts=FailureCounts(rate_limited=10, invalid_output=5),
            ),
        )
        output = format_full(stats)
        assert "Pool:" in output
        assert "Process exits" in output
        assert "Retry attempts by class" in output
        assert "Topology:       active=2 desired=3" in output
        assert "Queue:          active=24 pending=4 completed=90 failed=2 killed=0" in output
        assert "Worker caps:    desired=15 live=worker-0=15, worker-1=15" in output
        assert "Scale gen:      2" in output
        assert "Target:         28" in output
        assert "Ceilings:       memory=40 provider=28 operator=30" in output
        assert "Bottleneck:     DSQ-bound" in output
        assert "Recent 429s:    4" in output
        assert "Findings:" in output
        assert "[warning] pending_queue_below_target" in output
        assert "Starts:           120" in output
        assert "Successful (0):   115" in output
        assert "Failed (non-0):   5" in output
        assert "timeout:        3" in output
        assert "crash:          2" in output
        assert "rate_limited:     10" in output
        assert "invalid_output:   5" in output
