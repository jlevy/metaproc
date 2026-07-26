"""Pydantic models for unified run statistics.

All stats output is a serializable Pydantic model tree.
``RunStats.model_dump(mode="json")`` is the JSON output for both ``--json``
and ``/api/stats``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from metaproc.engine.run_status import TimingStats
from metaproc.runpool.concurrency import ConcurrencyPlan
from metaproc.runpool.status import FailureCounts, PressureStatus


class TimePoint(BaseModel):
    x: str
    y: float


class HourlyBucket(BaseModel):
    hour: str
    completed: int
    rate: float


class VariantProgress(BaseModel):
    completed: int
    running: int
    failed: int
    pending: int
    total: int
    pct: float
    timing: TimingStats | None = None


class ProgressStats(BaseModel):
    total_items: int
    completed: int
    failed: int
    running: int
    pending: int
    variants: dict[str, VariantProgress]
    wall_time_s: float
    eta_s: float | None = None


class ThroughputStats(BaseModel):
    items_per_hour: float
    recent_items_per_hour: float
    unique_completed: int
    total_process_runs: int
    retry_factor: float
    retry_pct: float
    hourly_breakdown: list[HourlyBucket]


class PoolStats(BaseModel):
    num_workers: int
    worker_ids: list[str]
    desired_workers: int | None = None
    active_count: int = 0
    pending_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    killed_count: int = 0
    current_concurrency: int
    max_concurrency: int
    desired_max_concurrency: int | None = None
    scale_generation: int | None = None
    live_worker_caps: dict[str, int] = Field(default_factory=dict)
    bottleneck: str | None = None
    provider_ceiling: int | None = None
    memory_ceiling: int | None = None
    operator_cap: int | None = None
    effective_target: int | None = None
    recent_rate_limits: int = 0
    pressure: PressureStatus | None = None
    concurrency_plan: ConcurrencyPlan | None = None
    worker_concurrency_plans: dict[str, ConcurrencyPlan] = Field(default_factory=dict)
    concurrency_adjustments: int
    findings: list[PoolFinding] = Field(default_factory=list)

    # Process-level exits (from runpool events): each entry is a hard
    # process termination — exit_code != 0 or a kill. ``process_starts``
    # minus ``process_successful_exits`` minus the kill total equals
    # ``process_failed_exits``.
    process_starts: int = 0
    process_successful_exits: int = 0
    process_failed_exits: int = 0
    process_failure_counts: FailureCounts = FailureCounts()
    process_kills_by_reason: dict[str, int] = Field(default_factory=dict)

    # Runner-level failure classifications (from live runpool-status.yaml,
    # aggregated across workers). Every failed attempt — kill, non-zero
    # exit, or exit=0 with invalid outputs — is classified once by
    # ``classify_failure()`` and tallied here. These are *attempts*, not
    # unique items: an item that is retried N times can contribute up to
    # N entries here.
    runner_failure_counts: FailureCounts = FailureCounts()


class PoolFinding(BaseModel):
    """Operator-facing runpool adequacy finding."""

    severity: str
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class APIStats(BaseModel):
    total_calls: int
    models: dict[str, int]
    total_cost_usd: float
    avg_calls_per_item: float
    error_count: int


class ResourceStats(BaseModel):
    avg_peak_rss_bytes: int
    max_peak_rss_bytes: int
    swap_used_gb: float
    kills_by_reason: dict[str, int]


class RunStats(BaseModel):
    """Unified stats for a run directory."""

    run_dir: str
    computed_at: datetime

    progress: ProgressStats | None = None
    throughput: ThroughputStats | None = None
    timing: TimingStats | None = None
    pool: PoolStats | None = None
    api: APIStats | None = None
    resources: ResourceStats | None = None


class RunPoolAnalysis(BaseModel):
    """Result of one-pass event analysis — shared by charts and stats."""

    # Taxonomy counts (for charts tally tree and stats)
    taxonomy_counts: dict[str, int]
    # Metadata (pool_id, backend, duration)
    metadata: dict[str, object]
    # Time-series data (for charts)
    pressure_series: list[TimePoint]
    running_count_series: list[TimePoint]
    concurrency_cap_series: list[TimePoint]
    # Aggregate stats (for stats engine)
    total_process_runs: int
    successful_exits: int
    failed_exits: int
    exit_durations_s: list[float]
    successful_exit_durations_s: list[float] = []
    successful_exit_timestamps: list[str] = []
    peak_rss_values: list[int]
    failure_counts: FailureCounts
    concurrency_adjustments: int
    kill_reasons: dict[str, int]
    # Annotations for charts
    annotations: list[dict[str, object]]
