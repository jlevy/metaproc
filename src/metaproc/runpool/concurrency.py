"""Structured RunPool concurrency-cap provenance."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from metaproc.osutils.memory_pressure import (
    MemoryPressure,
    available_memory_bytes_from_pressure,
)

CONCURRENCY_PLAN_SCHEMA = "metaproc.runpool.concurrency-plan/v1"
ConcurrencyLimitingFactor = Literal[
    "operator_cap",
    "initial_override",
    "min_concurrency",
    "host_cap",
    "memory_estimate",
    "profile_hint",
    "cli_cap",
    "batch_size",
]


class ConcurrencyPlan(BaseModel):
    """Why a RunPool started with its selected concurrency caps."""

    schema_id: Literal["metaproc.runpool.concurrency-plan/v1"] = CONCURRENCY_PLAN_SCHEMA
    backend: str
    execution_profile: str | None = None
    cli_max_concurrency: int | None = None
    batch_size: int
    requested_max_concurrency: int
    profile_max_concurrency_hint: int | None = None
    host_max_concurrency: int
    configured_max_concurrency: int
    operator_cap: int | None = None
    initial_concurrency_override: int | None = None
    initial_concurrency_estimate: int
    initial_concurrency: int
    min_concurrency: int
    estimated_process_rss_bytes: int
    initial_memory_budget_fraction: float
    initial_available_memory_bytes: int
    initial_total_memory_bytes: int
    limiting_factor: ConcurrencyLimitingFactor


def build_concurrency_plan(
    *,
    backend: str,
    execution_profile: str | None,
    cli_max_concurrency: int | None,
    batch_size: int | None,
    profile_max_concurrency_hint: int | None,
    host_max_concurrency: int | None,
    configured_max_concurrency: int,
    min_concurrency: int,
    initial_concurrency_override: int | None,
    initial_concurrency_estimate: int,
    selected_initial_concurrency: int,
    estimated_process_rss_bytes: int,
    initial_memory_budget_fraction: float,
    pressure: MemoryPressure,
    operator_cap: int | None = None,
) -> ConcurrencyPlan:
    """Build a serializable explanation for RunPool cap selection."""
    normalized_batch_size = batch_size or configured_max_concurrency
    requested_max_concurrency = cli_max_concurrency or normalized_batch_size
    resolved_host_cap = host_max_concurrency or configured_max_concurrency
    available_bytes = available_memory_bytes_from_pressure(pressure)
    normalized_rss_bytes = max(1, estimated_process_rss_bytes)

    return ConcurrencyPlan(
        backend=backend,
        execution_profile=execution_profile,
        cli_max_concurrency=cli_max_concurrency,
        batch_size=normalized_batch_size,
        requested_max_concurrency=requested_max_concurrency,
        profile_max_concurrency_hint=profile_max_concurrency_hint,
        host_max_concurrency=resolved_host_cap,
        configured_max_concurrency=configured_max_concurrency,
        operator_cap=operator_cap,
        initial_concurrency_override=initial_concurrency_override,
        initial_concurrency_estimate=initial_concurrency_estimate,
        initial_concurrency=selected_initial_concurrency,
        min_concurrency=min_concurrency,
        estimated_process_rss_bytes=normalized_rss_bytes,
        initial_memory_budget_fraction=initial_memory_budget_fraction,
        initial_available_memory_bytes=available_bytes,
        initial_total_memory_bytes=int(pressure.total_memory_gb * 1024**3),
        limiting_factor=_classify_limiting_factor(
            cli_max_concurrency=cli_max_concurrency,
            requested_max_concurrency=requested_max_concurrency,
            profile_max_concurrency_hint=profile_max_concurrency_hint,
            host_max_concurrency=resolved_host_cap,
            configured_max_concurrency=configured_max_concurrency,
            min_concurrency=min_concurrency,
            initial_concurrency_override=initial_concurrency_override,
            selected_initial_concurrency=selected_initial_concurrency,
            initial_concurrency_estimate=initial_concurrency_estimate,
            operator_cap=operator_cap,
        ),
    )


def _classify_limiting_factor(
    *,
    cli_max_concurrency: int | None,
    requested_max_concurrency: int,
    profile_max_concurrency_hint: int | None,
    host_max_concurrency: int,
    configured_max_concurrency: int,
    min_concurrency: int,
    initial_concurrency_override: int | None,
    selected_initial_concurrency: int,
    initial_concurrency_estimate: int,
    operator_cap: int | None,
) -> ConcurrencyLimitingFactor:
    effective_start_cap = min(selected_initial_concurrency, host_max_concurrency)

    if (
        operator_cap is not None
        and operator_cap < configured_max_concurrency
        and operator_cap <= selected_initial_concurrency
    ):
        return "operator_cap"

    if (
        host_max_concurrency < configured_max_concurrency
        and host_max_concurrency <= selected_initial_concurrency
    ):
        return "host_cap"

    if (
        initial_concurrency_override is not None
        and initial_concurrency_override < configured_max_concurrency
        and initial_concurrency_override <= selected_initial_concurrency
    ):
        return "initial_override"

    if (
        selected_initial_concurrency == min_concurrency
        and initial_concurrency_estimate <= min_concurrency
    ):
        return "min_concurrency"

    if (
        initial_concurrency_override is None
        and initial_concurrency_estimate < configured_max_concurrency
        and initial_concurrency_estimate <= effective_start_cap
    ):
        return "memory_estimate"

    if (
        profile_max_concurrency_hint is not None
        and profile_max_concurrency_hint < requested_max_concurrency
        and configured_max_concurrency == profile_max_concurrency_hint
    ):
        return "profile_hint"
    if cli_max_concurrency is not None:
        return "cli_cap"
    return "batch_size"
