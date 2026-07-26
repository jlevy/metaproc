"""Tests for structured RunPool concurrency-cap provenance."""

from __future__ import annotations

from typing import Any

from metaproc.osutils.memory_pressure import MemoryPressure, PressureLevel
from metaproc.runpool.concurrency import ConcurrencyPlan, build_concurrency_plan
from metaproc.runpool.event_models import PoolStartEvent
from metaproc.runpool.status import (
    PressureStatus,
    RunPoolStatus,
)
from metaproc.stats.models import PoolStats


def _pressure(*, available_pct: float = 25.0, total_memory_gb: float = 32.0) -> MemoryPressure:
    return MemoryPressure(
        available_pct=available_pct,
        swap_used_gb=0.0,
        total_memory_gb=total_memory_gb,
        level=PressureLevel.NORMAL,
        source="test",
    )


def _plan(**overrides: Any) -> ConcurrencyPlan:
    defaults: dict[str, Any] = {
        "backend": "local",
        "execution_profile": None,
        "cli_max_concurrency": None,
        "batch_size": 10,
        "profile_max_concurrency_hint": None,
        "host_max_concurrency": 10,
        "configured_max_concurrency": 10,
        "min_concurrency": 1,
        "initial_concurrency_override": None,
        "initial_concurrency_estimate": 10,
        "selected_initial_concurrency": 10,
        "estimated_process_rss_bytes": 1024**3,
        "initial_memory_budget_fraction": 0.5,
        "pressure": _pressure(available_pct=100.0),
    }
    defaults.update(overrides)
    return build_concurrency_plan(**defaults)


def test_concurrency_plan_records_profile_and_memory_inputs() -> None:
    plan = build_concurrency_plan(
        backend="local",
        execution_profile="codex-gpt55",
        cli_max_concurrency=25,
        batch_size=40,
        profile_max_concurrency_hint=8,
        host_max_concurrency=6,
        configured_max_concurrency=8,
        min_concurrency=1,
        initial_concurrency_override=None,
        initial_concurrency_estimate=2,
        selected_initial_concurrency=2,
        estimated_process_rss_bytes=4 * 1024**3,
        initial_memory_budget_fraction=0.25,
        pressure=_pressure(available_pct=100.0),
    )

    assert plan.execution_profile == "codex-gpt55"
    assert plan.cli_max_concurrency == 25
    assert plan.batch_size == 40
    assert plan.requested_max_concurrency == 25
    assert plan.profile_max_concurrency_hint == 8
    assert plan.host_max_concurrency == 6
    assert plan.configured_max_concurrency == 8
    assert plan.initial_concurrency_estimate == 2
    assert plan.initial_concurrency == 2
    assert plan.estimated_process_rss_bytes == 4 * 1024**3
    assert plan.initial_memory_budget_fraction == 0.25
    assert plan.limiting_factor == "memory_estimate"


def test_concurrency_plan_distinguishes_profile_hint_from_batch_fallback() -> None:
    plan = build_concurrency_plan(
        backend="local",
        execution_profile="pi-glm-5",
        cli_max_concurrency=None,
        batch_size=12,
        profile_max_concurrency_hint=3,
        host_max_concurrency=3,
        configured_max_concurrency=3,
        min_concurrency=1,
        initial_concurrency_override=None,
        initial_concurrency_estimate=3,
        selected_initial_concurrency=3,
        estimated_process_rss_bytes=512 * 1024**2,
        initial_memory_budget_fraction=0.5,
        pressure=_pressure(available_pct=80.0),
    )

    assert plan.requested_max_concurrency == 12
    assert plan.configured_max_concurrency == 3
    assert plan.initial_concurrency == 3
    assert plan.limiting_factor == "profile_hint"


def test_concurrency_plan_records_explicit_initial_override() -> None:
    plan = build_concurrency_plan(
        backend="local",
        execution_profile=None,
        cli_max_concurrency=10,
        batch_size=10,
        profile_max_concurrency_hint=None,
        host_max_concurrency=10,
        configured_max_concurrency=10,
        min_concurrency=1,
        initial_concurrency_override=4,
        initial_concurrency_estimate=10,
        selected_initial_concurrency=4,
        estimated_process_rss_bytes=512 * 1024**2,
        initial_memory_budget_fraction=0.5,
        pressure=_pressure(available_pct=80.0),
    )

    assert plan.initial_concurrency_override == 4
    assert plan.initial_concurrency == 4
    assert plan.limiting_factor == "initial_override"


def test_concurrency_plan_uses_provided_initial_estimate() -> None:
    plan = _plan(
        configured_max_concurrency=10,
        initial_concurrency_estimate=7,
        selected_initial_concurrency=7,
        estimated_process_rss_bytes=4 * 1024**3,
        initial_memory_budget_fraction=0.25,
        pressure=_pressure(available_pct=100.0, total_memory_gb=128.0),
    )

    assert plan.initial_concurrency_estimate == 7
    assert plan.limiting_factor == "memory_estimate"


def test_concurrency_plan_classifies_cli_cap() -> None:
    plan = _plan(
        cli_max_concurrency=6,
        batch_size=12,
        configured_max_concurrency=6,
        host_max_concurrency=6,
        initial_concurrency_estimate=6,
        selected_initial_concurrency=6,
    )

    assert plan.requested_max_concurrency == 6
    assert plan.limiting_factor == "cli_cap"


def test_concurrency_plan_classifies_batch_size_fallback() -> None:
    plan = _plan(
        cli_max_concurrency=None,
        batch_size=7,
        configured_max_concurrency=7,
        host_max_concurrency=7,
        initial_concurrency_estimate=7,
        selected_initial_concurrency=7,
    )

    assert plan.requested_max_concurrency == 7
    assert plan.limiting_factor == "batch_size"


def test_concurrency_plan_classifies_host_cap() -> None:
    plan = _plan(
        cli_max_concurrency=10,
        batch_size=10,
        configured_max_concurrency=10,
        host_max_concurrency=3,
        initial_concurrency_estimate=10,
        selected_initial_concurrency=10,
    )

    assert plan.host_max_concurrency == 3
    assert plan.limiting_factor == "host_cap"


def test_concurrency_plan_classifies_operator_cap() -> None:
    plan = _plan(
        configured_max_concurrency=10,
        operator_cap=4,
        initial_concurrency_estimate=10,
        selected_initial_concurrency=4,
    )

    assert plan.operator_cap == 4
    assert plan.limiting_factor == "operator_cap"


def test_concurrency_plan_classifies_min_concurrency_floor() -> None:
    plan = _plan(
        configured_max_concurrency=10,
        min_concurrency=2,
        initial_concurrency_estimate=1,
        selected_initial_concurrency=2,
    )

    assert plan.initial_concurrency == 2
    assert plan.limiting_factor == "min_concurrency"


def test_concurrency_plan_guards_zero_rss_for_serialization() -> None:
    plan = _plan(
        configured_max_concurrency=10,
        initial_concurrency_estimate=5,
        selected_initial_concurrency=5,
        estimated_process_rss_bytes=0,
    )

    assert plan.estimated_process_rss_bytes == 1


def test_status_event_and_stats_models_accept_optional_concurrency_plan() -> None:
    plan = build_concurrency_plan(
        backend="local",
        execution_profile="codex-gpt55",
        cli_max_concurrency=5,
        batch_size=5,
        profile_max_concurrency_hint=None,
        host_max_concurrency=5,
        configured_max_concurrency=5,
        min_concurrency=1,
        initial_concurrency_override=None,
        initial_concurrency_estimate=2,
        selected_initial_concurrency=2,
        estimated_process_rss_bytes=1024**3,
        initial_memory_budget_fraction=0.5,
        pressure=_pressure(),
    )
    status = RunPoolStatus(
        pool_id="pool",
        pid=1,
        started_at="2026-05-19T00:00:00+00:00",
        updated_at="2026-05-19T00:00:01+00:00",
        backend="local",
        max_concurrency=5,
        current_concurrency=2,
        active_count=0,
        pending_count=0,
        completed_count=0,
        failed_count=0,
        killed_count=0,
        pressure=PressureStatus(
            level=PressureLevel.NORMAL,
            available_pct=50.0,
            swap_used_gb=0.0,
            total_memory_gb=32.0,
            source="test",
        ),
        concurrency_plan=plan,
    )
    event = PoolStartEvent.model_validate(
        {
            "event": "pool_start",
            "ts": "2026-05-19T00:00:00+00:00",
            "pool_id": "pool",
            "backend": "local",
            "max_concurrency": 5,
            "initial_concurrency": 2,
            "concurrency_plan": plan.model_dump(mode="json"),
        }
    )
    pool_stats = PoolStats(
        num_workers=1,
        worker_ids=[],
        current_concurrency=2,
        max_concurrency=5,
        concurrency_adjustments=0,
        concurrency_plan=plan,
    )

    assert status.concurrency_plan == plan
    assert event.concurrency_plan == plan
    assert pool_stats.model_dump(mode="json")["concurrency_plan"]["execution_profile"] == (
        "codex-gpt55"
    )


def test_old_status_without_concurrency_plan_still_parses() -> None:
    status = RunPoolStatus.model_validate(
        {
            "pool_id": "pool",
            "pid": 1,
            "started_at": "2026-05-19T00:00:00+00:00",
            "updated_at": "2026-05-19T00:00:01+00:00",
            "backend": "local",
            "max_concurrency": 5,
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
                "total_memory_gb": 32.0,
                "source": "test",
            },
        }
    )

    assert status.concurrency_plan is None
