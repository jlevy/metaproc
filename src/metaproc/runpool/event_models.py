"""Typed Pydantic models for runpool JSONL events.

Discriminated union on the ``event`` field so each event type has its own
typed model. Replaces the ``dict[str, Any]`` pattern used in charts.py and
provides the shared foundation for both chart extraction and stats computation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from metaproc.runpool.concurrency import ConcurrencyPlan


class PoolStartEvent(BaseModel):
    event: Literal["pool_start"]
    ts: datetime
    pool_id: str
    backend: str
    max_concurrency: int
    initial_concurrency: int | None = None
    concurrency_plan: ConcurrencyPlan | None = None


class PoolShutdownEvent(BaseModel):
    event: Literal["pool_shutdown"]
    ts: datetime
    pool_id: str
    completed: int
    failed: int
    killed: int


class ProcessStartEvent(BaseModel):
    event: Literal["process_start"]
    ts: datetime
    pid: int | None = None
    external_id: str | None = None
    backend: str
    label: str


class ProcessExitEvent(BaseModel):
    event: Literal["process_exit"]
    ts: datetime
    pid: int | None = None
    external_id: str | None = None
    backend: str
    label: str
    exit_code: int | None = None
    elapsed_s: float
    peak_rss_bytes: int | None = None
    failure_class: str | None = None
    error: str | None = None


class ProcessKillEvent(BaseModel):
    event: Literal["process_kill"]
    ts: datetime
    pid: int | None = None
    external_id: str | None = None
    backend: str
    label: str
    kill_reason: str
    peak_rss_bytes: int | None = None


class HostSlotAcquiredEvent(BaseModel):
    event: Literal["host_slot_acquired"]
    ts: datetime
    namespace: str
    slot_id: int
    limit: int
    label: str
    lease_path: str


class HostSlotReleasedEvent(BaseModel):
    event: Literal["host_slot_released"]
    ts: datetime
    namespace: str
    slot_id: int
    limit: int
    label: str
    lease_path: str


class ConcurrencyAdjustEvent(BaseModel):
    event: Literal["concurrency_adjust"]
    ts: datetime
    old: int
    new: int
    reason: str
    consecutive_normal: int = 0
    consecutive_elevated: int = 0


class PressureCheckEvent(BaseModel):
    event: Literal["pressure_check"]
    ts: datetime
    level: str
    available_pct: float
    swap_used_gb: float | None = None
    total_memory_gb: float | None = None
    memory_level: str | None = None
    swap_delta_gb_per_min: float | None = None
    swap_level: str | None = None
    disk_free_gb: float | None = None
    disk_total_gb: float | None = None
    disk_used_pct: float | None = None
    disk_level: str | None = None
    disk_pressure_cause: str | None = None
    active_log_bytes: int | None = None


class ResourceTelemetryErrorEvent(BaseModel):
    event: Literal["resource_telemetry_error"]
    ts: datetime
    error: str
    action: str
    current_concurrency: int
    effective_target: int


class RetryScheduledEvent(BaseModel):
    event: Literal["retry_scheduled"]
    ts: datetime
    label: str
    attempt: int
    backoff_s: float


class AuthOutcomeEvent(BaseModel):
    """Schema-v2 ``auth_outcome`` event (plan-2026-05-03 internal issue).

    Permissive to support both schema-v1 (legacy, missing the join keys)
    and schema-v2 (full join keys); schema-v1 events default the
    additive fields to empty / None on read. Pre-2026-05-03 events
    have no ``schema_version`` field — pydantic accepts the default 1.
    """

    event: Literal["auth_outcome"]
    ts: datetime
    schema_version: int = 1
    adapter: str = ""
    label: str = ""
    slot_dir: str = ""
    bootstrap_fp: str = ""
    flush_fp: str = ""
    classification: str = ""
    cooling_until_ts: int | None = None
    rotated: bool = False
    retry_count: int = 0
    fallback_policy: str = ""
    reason: str = ""
    severity: str = ""
    known_bug_signature: str = ""
    pool_enabled: bool = True
    request_id: str = ""
    api_status: int | None = None
    oauth_refresh_status: int | None = None
    error_body_excerpt: str = ""
    retry_after_s: int | None = None
    # Schema-v2 join keys.
    run_id: str = ""
    step_id: str = ""
    item: str = ""
    attempt: int = 0
    session_log_path: str = ""


class AuthLeaseAcquiredEvent(BaseModel):
    """Schema-v2 ``auth_lease_acquired`` event (plan-2026-05-03 internal issue).

    Always schema-v2 — this event was added by internal issue so there
    is no legacy v1 shape to tolerate. Companion to
    :class:`AuthOutcomeEvent`; the two share the (run_id, step_id,
    item, attempt, session_log_path) primary-key tuple.
    """

    event: Literal["auth_lease_acquired"]
    ts: datetime
    schema_version: int = 2
    adapter: str
    label: str
    slot_dir: str = ""
    run_id: str = ""
    step_id: str = ""
    item: str = ""
    attempt: int = 0
    session_log_path: str = ""
    policy: str = ""
    active_lease_count: dict[str, int] = Field(default_factory=dict)


RunPoolEvent = Annotated[
    PoolStartEvent
    | PoolShutdownEvent
    | ProcessStartEvent
    | ProcessExitEvent
    | ProcessKillEvent
    | HostSlotAcquiredEvent
    | HostSlotReleasedEvent
    | ConcurrencyAdjustEvent
    | PressureCheckEvent
    | ResourceTelemetryErrorEvent
    | RetryScheduledEvent
    | AuthOutcomeEvent
    | AuthLeaseAcquiredEvent,
    Field(discriminator="event"),
]
"""Discriminated union of all runpool event types."""
