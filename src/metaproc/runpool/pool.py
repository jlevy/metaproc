"""RunPool — resource-aware process pool with adaptive concurrency.

The pool accepts ``ProcessConfig`` submissions and manages their lifecycle
through a ``LaunchBackend``.  Concurrency is controlled by an
``AdaptiveSemaphore`` that adjusts based on system memory pressure.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import shutil
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from pydantic import BaseModel, Field

from metaproc.io import read_yaml_file
from metaproc.models.lane import ExecutionLane
from metaproc.osutils.memory_pressure import (
    MemoryPressure,
    PressureLevel,
    UnsupportedTelemetryPlatformError,
    classify_swap_rate,
    estimate_initial_concurrency,
    max_pressure,
    measure,
    validate_supported_platform,
)
from metaproc.paths import (
    POOL_KILL_SENTINEL_FILE,
    POOL_STATUS_FILE,
    RUNPOOL_EVENTS_FILE,
    RUNPOOL_HEALTH_FILE,
    SCALE_OVERRIDE_FILE,
    SCALE_STATE_FILE,
    STATE_DIR,
)
from metaproc.runpool.backend import (
    LaunchBackend,
    LaunchHandle,
    LocalBackend,
    PreparedLaunch,
    get_log_size,
)
from metaproc.runpool.concurrency import ConcurrencyPlan, build_concurrency_plan
from metaproc.runpool.events import EventLogger
from metaproc.runpool.host_admission import (
    DEFAULT_HOST_ADMISSION_NAMESPACE,
    HostAdmissionGate,
    HostAdmissionLease,
)
from metaproc.runpool.semaphore import AdaptiveSemaphore
from metaproc.runpool.status import (
    ControllerStatus,
    FailureCounts,
    LaneStatus,
    PressureStatus,
    ProcessStatus,
    RunPoolStatus,
    ScaleState,
    read_scale_override,
    read_scale_state,
    write_scale_state,
    write_status,
)
from metaproc.settings import (
    POOL_ESTIMATED_PROCESS_RSS_BYTES,
    POOL_MAX_CONCURRENCY,
    POOL_MIN_CONCURRENCY,
)

log = logging.getLogger(__name__)


DISK_PRESSURE_ELEVATED_FREE_GB = 12.0
DISK_PRESSURE_HIGH_FREE_GB = 8.0
DISK_PRESSURE_CRITICAL_FREE_GB = 5.0
ACTIVE_LOG_PRESSURE_BYTES = 1 * 1024**3


@dataclass(frozen=True)
class SystemHealthSample:
    """Runpool resource heartbeat used for telemetry and launch throttling."""

    pressure: MemoryPressure
    disk_free_gb: float
    disk_total_gb: float
    disk_used_pct: float
    disk_level: PressureLevel
    disk_pressure_cause: str
    swap_delta_gb_per_min: float
    swap_level: PressureLevel

    @property
    def level(self) -> PressureLevel:
        """Combined pressure level used to scale agent concurrency.

        Memory + swap only. Disk is excluded: reducing concurrent agents
        does not free disk — per-step write volume is independent of
        parallelism, so fewer agents just spreads the same writes over
        more wall-clock time. Disk-too-low is enforced separately at the
        preflight gate (METAPROC_PREFLIGHT_MIN_DISK_GB), not by clamping
        concurrency.

        Common misunderstanding: a `level=critical` reading on a
        SystemHealthSample where ``disk_level == CRITICAL`` but
        ``pressure.level == NORMAL`` will NOT trigger concurrency
        adjustments here. Use ``combined_level_including_disk`` if you
        need the disk-inclusive signal for telemetry.
        """
        return max_pressure(self.pressure.level, self.swap_level)

    @property
    def combined_level_including_disk(self) -> PressureLevel:
        """Visibility/telemetry signal that folds disk into the combined level."""
        return max_pressure(self.pressure.level, self.disk_level, self.swap_level)

    @property
    def source(self) -> str:
        return (
            self.pressure.source
            + ("+swap-rate" if self.swap_level != PressureLevel.NORMAL else "")
            + ("+disk" if self.disk_level != PressureLevel.NORMAL else "")
        )


def _scale_override_paths_for_state_dir(state_dir: Path | None) -> list[Path]:
    """Return run-level then pool-local override paths for a pool state dir."""
    if state_dir is None:
        return []

    paths: list[Path] = []
    for candidate in (state_dir, *state_dir.parents):
        if candidate.name == STATE_DIR:
            paths.append(candidate / SCALE_OVERRIDE_FILE)
            break

    local_path = state_dir / SCALE_OVERRIDE_FILE
    if local_path not in paths:
        paths.append(local_path)
    return paths


def resolve_estimated_process_rss_bytes(
    runtime_config: Mapping[str, object],
    *,
    default: int = POOL_ESTIMATED_PROCESS_RSS_BYTES,
) -> int:
    """Resolve optional per-process RSS estimate from runtime config."""
    raw_bytes = runtime_config.get("estimated_process_rss_bytes")
    if raw_bytes is not None:
        value = int(str(raw_bytes))
        if value <= 0:
            raise ValueError("estimated_process_rss_bytes must be > 0")
        return value

    raw_mb = runtime_config.get("estimated_process_rss_mb")
    if raw_mb is not None:
        value_mb = int(str(raw_mb))
        if value_mb <= 0:
            raise ValueError("estimated_process_rss_mb must be > 0")
        return value_mb * 1024 * 1024

    return default


def resolve_initial_memory_budget_fraction(
    runtime_config: Mapping[str, object],
    *,
    default: float = 0.5,
) -> float:
    """Resolve optional startup memory-budget fraction from runtime config."""
    raw = runtime_config.get("initial_memory_budget_fraction")
    if raw is None:
        return default
    value = float(str(raw))
    if not 0 < value <= 1:
        raise ValueError("initial_memory_budget_fraction must be > 0 and <= 1")
    return value


def resolve_host_max_concurrency(
    runtime_config: Mapping[str, object],
    *,
    default: int,
) -> int:
    """Resolve optional host-wide local launch cap from runtime config.

    Precedence:
    1. ``METAPROC_HOST_MAX_LOCAL_AGENTS`` env var — HARD aggregate cap across
       ALL profiles sharing the host. Set this when running multiple parallel
       orchestrators on a single machine to bound TOTAL concurrent agents
       (the per-profile ``host_max_concurrency`` cannot coordinate across
       profiles by itself).
    2. ``host_max_concurrency`` from the profile's resources.
    3. ``default`` argument (typically the pool's ``max_concurrency``).

    Returns ``min(env_var, profile_or_default)`` so the env var ALWAYS wins
    when set and lower than the profile cap. This makes the env var a true
    aggregate ceiling — operators can deploy a 32 GB Mac with
    ``METAPROC_HOST_MAX_LOCAL_AGENTS=10`` and run 9 parallel orchestrators
    without over-subscribing memory regardless of per-profile caps.
    """
    raw = runtime_config.get("host_max_concurrency")
    if raw is None:
        profile_value = default
    else:
        profile_value = int(str(raw))
        if profile_value <= 0:
            raise ValueError("host_max_concurrency must be > 0")
    env_raw = os.environ.get("METAPROC_HOST_MAX_LOCAL_AGENTS")
    if env_raw:
        try:
            env_value = int(env_raw)
            if env_value <= 0:
                raise ValueError("METAPROC_HOST_MAX_LOCAL_AGENTS must be > 0")
        except ValueError as exc:
            log.warning(
                "Ignoring invalid METAPROC_HOST_MAX_LOCAL_AGENTS=%r: %s",
                env_raw,
                exc,
            )
            return profile_value
        if env_value < profile_value:
            log.info(
                "Capping host_max_concurrency from profile=%d to "
                "METAPROC_HOST_MAX_LOCAL_AGENTS=%d (cross-pool aggregate cap)",
                profile_value,
                env_value,
            )
            return env_value
    return profile_value


def resolve_min_concurrency(
    runtime_config: Mapping[str, object],
    *,
    default: int,
) -> int:
    """Resolve optional per-profile min_concurrency floor from runtime config.

    A profile can override the global ``POOL_MIN_CONCURRENCY``
    floor (default 2). Values < 1 are rejected at validate time.
    """
    raw = runtime_config.get("min_concurrency")
    if raw is None:
        return default
    value = int(str(raw))
    if value < 1:
        raise ValueError("min_concurrency must be >= 1")
    return value


def _classify_disk_free(free_gb: float) -> PressureLevel:
    """Classify local disk headroom for run artifact writes."""
    if free_gb < DISK_PRESSURE_CRITICAL_FREE_GB:
        return PressureLevel.CRITICAL
    if free_gb < DISK_PRESSURE_HIGH_FREE_GB:
        return PressureLevel.HIGH
    if free_gb < DISK_PRESSURE_ELEVATED_FREE_GB:
        return PressureLevel.ELEVATED
    return PressureLevel.NORMAL


def _classify_disk_pressure_cause(
    *,
    pressure: MemoryPressure,
    disk_level: PressureLevel,
    swap_level: PressureLevel,
    active_log_bytes: int,
) -> str:
    """Explain low-disk pressure without conflating it with memory pressure."""
    if disk_level == PressureLevel.NORMAL:
        return "none"
    if swap_level != PressureLevel.NORMAL:
        return "swap_growth"
    if pressure.swap_used_gb >= max(2.0, pressure.total_memory_gb * 0.75):
        return "swap_reserve_high"
    if active_log_bytes >= ACTIVE_LOG_PRESSURE_BYTES:
        return "active_logs"
    return "low_disk_unknown"


# ── Configuration ───────────────────────────────────────────────


class RunPoolConfig(BaseModel):
    """Configuration for the process pool.

    model_config: arbitrary_types_allowed is needed for the external_semaphore
    field which holds an asyncio.Semaphore (not Pydantic-serializable).

    Adaptive concurrency policy
    ---------------------------
    The pool adjusts concurrency based on system memory pressure, checked
    every ``pressure_check_interval_s`` seconds.  Adjustments are
    proportional to current capacity so behaviour scales naturally.

    =========  ========  ===========  ==========================================
    Level      Factor    Hysteresis   Behaviour
    =========  ========  ===========  ==========================================
    NORMAL     +10%      3 ticks      Ramp up after sustained low pressure.
    ELEVATED   hold      none         Hold current concurrency under moderate
                                      pressure.  This avoids asymmetric collapse
                                      when a machine sits near the threshold.
    HIGH       −25%      none         Immediate reduction every tick.
    CRITICAL   −50%      none         Aggressive reduction every tick.  Future:
                                      also sheds (kills) youngest excess
                                      processes when held > capacity.
    =========  ========  ===========  ==========================================

    Hysteresis counters reset after each adjustment, so the pool waits
    another ``hysteresis_checks`` ticks before adjusting again.  This
    gives newly launched (or killed) processes time to show their memory
    impact before the next decision.

    Startup concurrency
    -------------------
    When ``initial_concurrency`` is 0 (default), the pool auto-estimates
    a safe starting value from current free memory at startup:

        initial = (free_memory × initial_memory_budget_fraction)
                  / POOL_ESTIMATED_PROCESS_RSS_BYTES

    This adapts to the machine — ~25 on a 32 GB laptop, ~58 on a 64 GB
    VM, ~117 on a 128 GB VM.  Set ``initial_concurrency`` explicitly to
    override.  See ``estimate_initial_concurrency()`` in
    ``osutils/memory_pressure.py`` and defaults in ``settings.py``.
    """

    model_config = {"arbitrary_types_allowed": True}

    max_concurrency: int = POOL_MAX_CONCURRENCY
    initial_concurrency: int = 0  # 0 = auto-estimate from free memory; see docstring
    min_concurrency: int = POOL_MIN_CONCURRENCY
    estimated_process_rss_bytes: int = POOL_ESTIMATED_PROCESS_RSS_BYTES
    initial_memory_budget_fraction: float = 0.5
    monitor_interval_s: float = 10.0
    pressure_check_interval_s: float = 10.0
    ramp_up_factor: float = 0.1
    elevated_down_factor: float = 0.1
    ramp_down_factor: float = 0.25
    critical_down_factor: float = 0.5
    hysteresis_checks: int = 3
    rate_limit_burst_threshold: int = 3
    rate_limit_burst_window_s: float = 30.0
    rate_limit_down_factor: float = 0.5
    state_dir: Path | None = None
    logs_dir: Path | None = None
    recent_completions_limit: int = 20
    external_semaphore: asyncio.Semaphore | None = None
    host_admission_enabled: bool = False
    host_admission_dir: Path | None = None
    host_admission_namespace: str = DEFAULT_HOST_ADMISSION_NAMESPACE
    host_admission_limit: int | None = None
    host_admission_poll_interval_s: float = 1.0
    execution_profile: str | None = None
    cli_max_concurrency: int | None = None
    batch_size: int | None = None
    profile_max_concurrency_hint: int | None = None
    execution_lanes: list[ExecutionLane] = Field(default_factory=list)
    """Registered execution-lane rows the pool may schedule tasks across.

    Empty for the degenerate single-profile case — the pool synthesizes
    one lane from ``execution_profile`` so status readers always see at
    least one row. When set, the pool emits per-lane
    :class:`LaneStatus` rows in its status snapshot."""


def _build_lane_registry(config: RunPoolConfig) -> dict[str, ExecutionLane]:
    """Return an ordered ``lane_id -> ExecutionLane`` registry for the pool.

    When no lanes are declared the registry contains a single synthesized
    entry derived from ``RunPoolConfig.execution_profile`` so degenerate
    single-profile pools still report lane status uniformly. ``adapter``
    is left blank on the synthesized lane because the pool itself does
    not know which adapter the orchestrator chose; lane-native callers
    pass real :class:`ExecutionLane` rows from
    ``metaproc.engine.lane_expand.materialize_execution_lanes`` and
    those carry the resolved adapter.
    """
    registry: dict[str, ExecutionLane] = {}
    for lane in config.execution_lanes:
        registry[lane.lane_id] = lane
    if not registry and config.execution_profile:
        registry[config.execution_profile] = ExecutionLane(
            lane_id=config.execution_profile,
            execution_profile=config.execution_profile,
        )
    return registry


# ── Process config and result (frozen dataclasses) ──────────────


@dataclass(frozen=True)
class ProcessConfig:
    """Configuration for a single process to manage.

    Exactly one of ``launch`` or ``prepare_launch`` must be set.

    ``lane_id`` and ``execution_profile`` route the task instance to its
    execution lane in pool admission, status reporting, and trace
    metadata. Lane-aware orchestrators populate them from
    :class:`metaproc.models.lane.ExecutionLane`; the degenerate single-lane
    case passes the synthesized lane id from the plan.
    """

    launch: PreparedLaunch | None = None
    prepare_launch: Callable[[], PreparedLaunch] | None = None
    timeout_s: float | None = None
    stall_timeout_s: float | None = None
    max_rss_bytes: int | None = None
    max_log_bytes: int | None = None
    max_descendants: int | None = None
    label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    lane_id: str | None = None
    execution_profile: str | None = None

    def resolve_launch(self) -> PreparedLaunch:
        """Return the PreparedLaunch, calling prepare_launch if needed."""
        if self.launch is not None:
            return self.launch
        if self.prepare_launch is not None:
            return self.prepare_launch()
        raise ValueError("ProcessConfig must have either launch or prepare_launch set")


@dataclass(frozen=True)
class ProcessResult:
    """Result of a managed process."""

    config: ProcessConfig
    pid: int | None
    external_id: str | None
    backend: str
    exit_code: int | None
    kill_reason: str | None
    elapsed_s: float
    peak_rss_bytes: int | None
    peak_descendants: int | None
    log_size_bytes: int | None


# ── Internal tracking ───────────────────────────────────────────


@dataclass
class _ActiveProcess:
    """Mutable tracking state for a running process."""

    config: ProcessConfig
    handle: LaunchHandle
    prepared: PreparedLaunch
    start_time: float  # monotonic
    started_at: str  # ISO 8601 wall clock
    peak_rss_bytes: int = 0
    peak_descendants: int = 0
    current_rss_bytes: int = 0
    current_descendants: int = 0
    current_log_bytes: int = 0
    last_log_bytes: int = 0
    last_log_change_time: float = 0.0  # monotonic; set to start_time initially
    killed: bool = False
    kill_reason: str | None = None


# ── RunPool ─────────────────────────────────────────────────────


class RunPool:
    """Resource-aware process pool with adaptive concurrency."""

    def __init__(
        self,
        pool_config: RunPoolConfig | None = None,
        backend: LaunchBackend | None = None,
    ) -> None:
        self._config = pool_config or RunPoolConfig()
        self._backend: LaunchBackend = backend or LocalBackend()
        self._backend_name = str(self._backend.name)
        try:
            initial_pressure = validate_supported_platform()
        except UnsupportedTelemetryPlatformError as exc:
            message = f"RunPool requires reliable resource telemetry before launching agents. {exc}"
            raise UnsupportedTelemetryPlatformError(message) from exc
        initial_concurrency_estimate = estimate_initial_concurrency(
            self._config.max_concurrency,
            self._config.estimated_process_rss_bytes,
            budget_fraction=self._config.initial_memory_budget_fraction,
            pressure=initial_pressure,
        )
        if self._config.initial_concurrency > 0:
            starting = min(self._config.initial_concurrency, self._config.max_concurrency)
        else:
            starting = initial_concurrency_estimate
        self._pool_id = (
            f"pool-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
            f"-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )
        configured_host_limit = self._config.host_admission_limit or self._config.max_concurrency
        self._host_admission: HostAdmissionGate | None = (
            HostAdmissionGate(
                root_dir=self._config.host_admission_dir,
                namespace=self._config.host_admission_namespace,
                limit=configured_host_limit,
                poll_interval_s=self._config.host_admission_poll_interval_s,
            )
            if self._config.host_admission_enabled and self._backend_name == "local"
            else None
        )
        active_host_limit = (
            configured_host_limit
            if self._config.host_admission_enabled and self._backend_name == "local"
            else self._config.max_concurrency
        )

        self._started_at = datetime.now(UTC).isoformat(timespec="seconds")

        # Tracking state.
        self._active: dict[int | str, _ActiveProcess] = {}  # keyed by pid or external_id
        self._pending_count = 0
        self._completed_count = 0
        self._failed_count = 0
        self._killed_count = 0
        self._failure_class_counts: dict[str, int] = {}
        self._pending_retries = 0
        self._recent_completions: list[ProcessStatus] = []

        # Per-lane tracking. A degenerate (no lane_matrix) pool synthesizes
        # one lane from ``execution_profile`` so consumers can read lane
        # status uniformly. The lane registry is the authoritative ordering
        # for status output.
        self._lane_registry: dict[str, ExecutionLane] = _build_lane_registry(self._config)
        self._lane_counters: dict[str, dict[str, int]] = {
            lane_id: {"active": 0, "completed": 0, "failed": 0, "killed": 0}
            for lane_id in self._lane_registry
        }

        # Event log and status file.
        self._event_logger: EventLogger | None = None
        self._health_logger: EventLogger | None = None
        self._status_path: Path | None = None
        self._scale_state_path: Path | None = None
        self._scale_override_paths: list[Path] = []
        self._operator_cap_override: int | None = None
        self._operator_mode_override: str | None = None
        self._max_concurrency_override: int | None = None
        if self._config.state_dir is not None:
            self._config.state_dir.mkdir(parents=True, exist_ok=True)
            self._status_path = self._config.state_dir / POOL_STATUS_FILE
            self._scale_state_path = self._config.state_dir / SCALE_STATE_FILE
            self._scale_override_paths = _scale_override_paths_for_state_dir(self._config.state_dir)
            # Clear stale kill sentinel from a previous run.
            stale_sentinel = self._config.state_dir / POOL_KILL_SENTINEL_FILE
            if stale_sentinel.exists():
                stale_sentinel.unlink()
                log.info("Cleared stale kill sentinel from previous run")
            # Clear stale runpool-status.yaml from a dead prior orchestrator
            # . Otherwise `metaproc pool rollup` and operators
            # see the prior orchestrator's plan values (e.g. old profile RSS,
            # old host_max) until the new orchestrator writes its first status
            # snapshot some seconds later. The 2026-05-24 three-lane batch hit
            # this: codex profile was updated 4096→1024 MB mid-batch, but
            # `pool rollup` kept showing 4096 because the stale status file
            # outlived the dead old orchestrator. Cleared status forces the
            # operator-facing view to "no plan yet" until the fresh write,
            # which is correct.
            if self._status_path.exists():
                try:
                    self._status_path.unlink()
                    log.info("Cleared stale runpool-status.yaml from previous run")
                except OSError:
                    log.warning(
                        "Failed to clear stale runpool-status.yaml at %s",
                        self._status_path,
                        exc_info=True,
                    )
            # Clear stale .partial atomic-write fragments.
            # If a prior orchestrator ENOSPC'd during atomic_output_file, the
            # .partial files can confuse the next atomic write's tmp-name.
            for partial in self._config.state_dir.glob("*.partial"):
                try:
                    partial.unlink()
                    log.info("Cleared stale atomic-write partial: %s", partial.name)
                except OSError:
                    pass
        if self._config.logs_dir is not None:
            self._config.logs_dir.mkdir(parents=True, exist_ok=True)
            self._event_logger = EventLogger(self._config.logs_dir / RUNPOOL_EVENTS_FILE)
            self._health_logger = EventLogger(self._config.logs_dir / RUNPOOL_HEALTH_FILE)

        # Honor pre-seeded operator caps before any submitted item can acquire
        # a launch slot. Runtime overrides are still refreshed on every
        # pressure tick.
        self._read_overrides()
        starting = max(self._config.min_concurrency, min(starting, self._operator_cap))
        self._concurrency_plan: ConcurrencyPlan = build_concurrency_plan(
            backend=self._backend_name,
            execution_profile=self._config.execution_profile,
            cli_max_concurrency=self._config.cli_max_concurrency,
            batch_size=self._config.batch_size,
            profile_max_concurrency_hint=self._config.profile_max_concurrency_hint,
            host_max_concurrency=active_host_limit,
            configured_max_concurrency=self._config.max_concurrency,
            min_concurrency=self._config.min_concurrency,
            initial_concurrency_override=(
                self._config.initial_concurrency if self._config.initial_concurrency > 0 else None
            ),
            initial_concurrency_estimate=initial_concurrency_estimate,
            selected_initial_concurrency=starting,
            estimated_process_rss_bytes=self._config.estimated_process_rss_bytes,
            initial_memory_budget_fraction=self._config.initial_memory_budget_fraction,
            pressure=initial_pressure,
            operator_cap=self._operator_cap_override,
        )
        self._semaphore = AdaptiveSemaphore(starting)

        # Asyncio state (set on first submit).
        self._started = False
        self._shutdown_event = asyncio.Event()
        self._monitor_task: asyncio.Task[None] | None = None

        # Pressure tracking for hysteresis.
        self._consecutive_normal = 0
        self._consecutive_elevated = 0
        self._consecutive_provider_clear = 0
        self._recent_rate_limits: deque[float] = deque()
        self._prev_pending_retries = 0
        self._last_swap_sample: tuple[float, float] | None = None
        self._latest_health: SystemHealthSample | None = None
        self._memory_ceiling = starting
        self._provider_ceiling = starting

        # Quota pause state. Set by `pause_for_quota` when a quota-exhausted
        # signal lands; submissions block on `_quota_paused_event` until the
        # named reset window passes (the 2026-05-13 cascade fix).
        self._quota_paused_until: datetime | None = None
        self._quota_paused_event: asyncio.Event | None = None
        self._quota_pause_task: asyncio.Task[None] | None = None

        # Restore governor state from a previous run if available.
        #
        # `memory_ceiling` is deliberately not restored. It describes the host,
        # not the run, and the host is free to have changed completely since the
        # saved state was written: a ceiling of 79 earned on a quiet machine says
        # nothing about a machine now holding 3 processes' worth of headroom.
        # Restoring it verbatim reproduces the burst-on-resume that took a 34 GB
        # host from a fresh estimate of 3 to a restored ceiling of 79, which at
        # 1.5 GB per process is 118 GB of intent on 34 GB of RAM.
        #
        # Starting from the fresh estimate costs a ramp, and the ramp is fast.
        # It also composes correctly with a resume, where processes already in
        # flight are consuming memory that the fresh reading has by definition
        # already accounted for.
        #
        # This subsumes the older floor-up rule, which raised a restored ceiling
        # to the fresh estimate when it was lower and left it alone when it was
        # higher. That protected an operator raising `--max-concurrency` from
        # inheriting a low ceiling, and did nothing about the direction that
        # crashes hosts. Both directions now resolve to the fresh estimate.
        #
        # `provider_ceiling` is sticky only while the saved controller state
        # still carries live provider pressure. A prior low-cap launch can
        # otherwise strand a higher-cap resume as DSQ-bound even after the
        # operator has raised the profile/run cap. When the old cap was lower,
        # the restored provider ceiling is below the fresh estimate, and there
        # are no recent rate limits or pending retries, treat it as stale and
        # floor it to the fresh estimate. Unlike memory, provider pressure is a
        # property of the run and the account rather than the host, so it is
        # worth carrying across a resume when it is still live.
        if self._scale_state_path is not None and self._scale_state_path.exists():
            try:
                prev = read_scale_state(self._scale_state_path)
                controller = prev.controller
                if controller.memory_ceiling > starting:
                    log.info(
                        "Discarded restored memory_ceiling=%d in favour of the fresh "
                        "estimate %d: the saved ceiling describes a host reading that "
                        "no longer holds",
                        controller.memory_ceiling,
                        starting,
                    )
                restored_memory = starting
                restored_provider = max(
                    self._config.min_concurrency,
                    min(controller.provider_ceiling, self._config.max_concurrency),
                )
                if (
                    restored_provider < starting
                    and controller.operator_cap < self._config.max_concurrency
                    and controller.recent_rate_limits == 0
                    and controller.pending_retries == 0
                ):
                    log.info(
                        "Floored stale restored provider_ceiling=%d to fresh estimate %d "
                        "(prior operator_cap=%d, max_concurrency=%d); saved state had "
                        "no recent rate limits or pending retries",
                        restored_provider,
                        starting,
                        controller.operator_cap,
                        self._config.max_concurrency,
                    )
                    restored_provider = starting
                self._memory_ceiling = restored_memory
                self._provider_ceiling = restored_provider
                restored_target = self._effective_target()
                self._semaphore.set_capacity(restored_target)
                log.info(
                    "Restored governor state from scale-state.yaml: "
                    "memory_ceiling=%d, provider_ceiling=%d, effective=%d",
                    self._memory_ceiling,
                    self._provider_ceiling,
                    restored_target,
                )
            except Exception:
                log.warning(
                    "Failed to read scale-state.yaml, using fresh governor state",
                    exc_info=True,
                )

    @property
    def pool_id(self) -> str:
        return self._pool_id

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def current_max_concurrency(self) -> int:
        return self._semaphore.capacity

    def record_failure_class(
        self,
        failure_class: str,
        *,
        quota_reset_at: datetime | None = None,
    ) -> None:
        """Record a failure classification for status reporting.

        Called by run-parallel after classifying error strings. The pool
        itself only sees exit codes; the caller has access to error details.

        ``quota_reset_at`` (optional) is honored when ``failure_class`` is
        ``"quota_exhausted"`` — the pool pauses submissions until the named
        reset window passes. Without it, the quota count is recorded but no
        pause occurs (caller couldn't parse a reset time).
        """
        self._failure_class_counts[failure_class] = (
            self._failure_class_counts.get(failure_class, 0) + 1
        )
        if failure_class == "rate_limited":
            self._apply_rate_limit_backoff()
        elif failure_class == "quota_exhausted" and quota_reset_at is not None:
            self.pause_for_quota(quota_reset_at)

    def pause_for_quota(
        self,
        reset_at: datetime,
        *,
        buffer_s: float = 30.0,
        progress_interval_s: float = 300.0,
    ) -> None:
        """Pause new process submissions until ``reset_at + buffer_s``.

        Replaces the per-task crash cascade with a single cohort-wide
        pause-and-resume. ``buffer_s`` adds a small grace period to avoid
        racing the reset; ``progress_interval_s`` controls how often the
        pause emits a "still paused" event (default every 5 minutes —
        operator can see the pool isn't stuck).

        Idempotent: if a later quota signal pushes ``reset_at`` further out,
        the pause is extended. An earlier ``reset_at`` is ignored (we don't
        shorten an existing pause; the original quota signal was authoritative).
        """
        if self._quota_paused_until is not None and reset_at <= self._quota_paused_until:
            return
        self._quota_paused_until = reset_at
        if self._quota_paused_event is None:
            self._quota_paused_event = asyncio.Event()
        # Clear so submissions block until the pause-task resolves it.
        self._quota_paused_event.clear()
        log.warning(
            "RunPool: pausing for quota reset until %s (+ %.0fs buffer)",
            reset_at.isoformat(),
            buffer_s,
        )
        if self._event_logger is not None:
            self._event_logger.quota_pause_started(reset_at.isoformat(), buffer_s)
        # Spawn a task to wait out the pause + emit progress events. Skip
        # spawning if there's no running loop (e.g. tests that exercise
        # pause logic synchronously); the gate still blocks submissions
        # because _quota_paused_event is cleared.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._quota_pause_task is not None and not self._quota_pause_task.done():
            self._quota_pause_task.cancel()
        self._quota_pause_task = loop.create_task(
            self._wait_out_quota_pause(buffer_s, progress_interval_s)
        )

    async def _await_quota_pause(self) -> None:
        """Block until any active quota pause is lifted. No-op when no pause."""
        if self._quota_paused_event is None:
            return
        if self._quota_paused_event.is_set():
            return
        await self._quota_paused_event.wait()

    async def _wait_out_quota_pause(self, buffer_s: float, progress_interval_s: float) -> None:
        """Sleep until ``_quota_paused_until + buffer_s``, emitting periodic
        progress events. On completion, clears the pause state and unblocks
        the gate so submissions can resume.
        """
        try:
            while True:
                paused_until = self._quota_paused_until
                if paused_until is None:
                    break
                now = datetime.now(paused_until.tzinfo)
                remaining_s = (paused_until - now).total_seconds() + buffer_s
                if remaining_s <= 0:
                    break
                step_s = min(progress_interval_s, max(1.0, remaining_s))
                await asyncio.sleep(step_s)
                paused_until = self._quota_paused_until
                if paused_until is None or self._event_logger is None:
                    continue
                remaining = (paused_until - datetime.now(paused_until.tzinfo)).total_seconds()
                if remaining > 0:
                    self._event_logger.quota_pause_tick(round(remaining, 1))
        except asyncio.CancelledError:
            return
        # Clear pause state and unblock submissions.
        self._quota_paused_until = None
        if self._event_logger is not None:
            self._event_logger.quota_pause_resumed()
        log.info("RunPool: quota pause resumed")
        if self._quota_paused_event is not None:
            self._quota_paused_event.set()

    def _apply_rate_limit_backoff(self) -> None:
        """Reduce concurrency when rate-limit failures burst.

        Memory pressure is still the primary resource signal, but some providers
        will return 429s long before the host is memory-bound. When a burst of
        recent rate-limit failures crosses the configured threshold, treat it as
        an external-pressure signal and cut concurrency immediately. Recovery is
        gated by a cooldown so the normal pressure-based ramp-up loop does not
        immediately undo the provider backoff.
        """
        now = time.monotonic()
        self._recent_rate_limits.append(now)
        self._prune_recent_rate_limits(now)

        if len(self._recent_rate_limits) < self._config.rate_limit_burst_threshold:
            return

        old_cap = self._semaphore.capacity
        decrement = max(1, math.ceil(old_cap * self._config.rate_limit_down_factor))
        burst_floor = max(self._config.min_concurrency, self._config.max_concurrency // 4)
        new_provider_ceiling = max(old_cap - decrement, burst_floor)
        self._provider_ceiling = min(self._provider_ceiling, new_provider_ceiling)
        self._consecutive_provider_clear = 0
        latest = self._recent_rate_limits[-1]
        self._recent_rate_limits.clear()
        self._recent_rate_limits.append(latest)
        new_cap = self._effective_target()

        if new_cap == old_cap:
            self._write_status()
            return

        self._consecutive_normal = 0
        self._consecutive_elevated = 0
        self._set_capacity(new_cap, reason="rate_limit_burst")
        self._write_status()

    def record_retry_scheduled(self, label: str, attempt: int, backoff_s: float) -> None:
        """Record that an item has been scheduled for retry after backoff.

        Increments the pending retries counter, emits a retry_scheduled event,
        and triggers a status write.  Called by the orchestrator when pushing
        to the retry heap.
        """
        self._pending_retries += 1
        if self._event_logger is not None:
            self._event_logger.retry_scheduled(label, attempt, backoff_s)
        self._write_status()

    def record_retry_consumed(self, label: str) -> None:
        """Record that a pending retry has been submitted to the pool.

        Decrements the pending retries counter and triggers a status write.
        Called by the orchestrator when popping from the retry heap to submit.
        """
        self._pending_retries = max(0, self._pending_retries - 1)
        self._write_status()

    def record_auth_outcome(self, outcome: dict[str, object]) -> None:
        """Emit a per-item auth outcome onto the runpool event stream.

        No-op when no event logger is attached (e.g. a pool started
        without a logs dir). Accepts the dataclass-to-dict form of
        :class:`~metaproc.dispatch.pool_dispatch.AuthOutcome` — the
        caller already has the AuthOutcome and knows which retry
        count / fallback policy to stamp onto it.
        """
        if self._event_logger is not None:
            self._event_logger.auth_outcome(outcome)

    def record_auth_lease_acquired(self, payload: dict[str, object]) -> None:
        """Emit a per-item auth-lease-acquired event (schema-v2).

        No-op when no event logger is attached. Mirror of
        :meth:`record_auth_outcome` for the acquisition-time companion
        event — the slot coordinator writes one of these before each
        subprocess spawn so post-hoc analysis can pair the lease with
        the eventual outcome by primary key.
        """
        if self._event_logger is not None:
            self._event_logger.auth_lease_acquired(payload)

    def _start(self) -> None:
        """One-time startup: open event log, start monitor, write initial status."""
        if self._started:
            return
        self._started = True
        if self._event_logger is not None:
            self._event_logger.open()
            self._event_logger.pool_start(
                self._pool_id,
                self._backend_name,
                self._config.max_concurrency,
                self._semaphore.capacity,
                concurrency_plan=self._concurrency_plan.model_dump(mode="json"),
            )
        if self._health_logger is not None:
            self._health_logger.open()
        self._monitor_task = asyncio.create_task(self._pressure_monitor_loop())
        self._write_status()

    def submit(self, config: ProcessConfig) -> asyncio.Future[ProcessResult]:
        """Queue one process and return a future that resolves on completion."""
        self._start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ProcessResult] = loop.create_future()
        self._pending_count += 1
        asyncio.create_task(self._run_process(config, future))
        return future

    def submit_many(self, configs: list[ProcessConfig]) -> list[asyncio.Future[ProcessResult]]:
        """Queue multiple processes and return one future per submission."""
        return [self.submit(config) for config in configs]

    async def submit_batch(self, configs: list[ProcessConfig]) -> list[ProcessResult]:
        """Convenience: submit many and wait for all results."""
        futures = self.submit_many(configs)
        return [await f for f in asyncio.as_completed(futures)]

    async def __aenter__(self) -> Self:
        """Enter the pool as a context manager.

        Nothing to set up: the pool is usable from construction. This exists so the
        matching ``__aexit__`` can guarantee shutdown, which is the part callers
        outside this package get wrong when they hand-roll a ``finally``.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Shut down on the way out, including when the body raised.

        Leaving a pool unshut leaks the monitor task, the event log handle, and any
        host admission slots its processes hold, and a held slot is invisible capacity
        loss for every other run on the machine.
        """
        await self.shutdown()

    async def shutdown(self, timeout_s: float = 30.0) -> None:
        """Graceful shutdown: wait for running processes, then kill stragglers."""
        self._shutdown_event.set()

        # Wait for all active processes to finish.
        if self._active:
            deadline = time.monotonic() + timeout_s
            while self._active and time.monotonic() < deadline:
                await asyncio.sleep(0.5)

        # Kill any remaining processes.
        for _key, active in list(self._active.items()):
            log.warning("Killing process %s on shutdown", active.config.label)
            active.killed = True
            active.kill_reason = "shutdown"
            await self._backend.kill(active.handle)

        # Give run_process tasks time to notice exits and clean up.
        if self._active:
            kill_deadline = time.monotonic() + 5.0
            while self._active and time.monotonic() < kill_deadline:
                await asyncio.sleep(0.1)

        # Stop the monitor task.
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task

        # Write final status and close event log.
        self._write_status()
        if self._event_logger is not None:
            self._event_logger.pool_shutdown(
                self._pool_id,
                self._completed_count,
                self._failed_count,
                self._killed_count,
            )
            self._event_logger.close()
        if self._health_logger is not None:
            self._health_logger.close()

    @property
    def snapshot(self) -> RunPoolStatus:
        """Current pool state — same structure written to the status file."""
        return self._build_status()

    # ── Internal ────────────────────────────────────────────────

    async def _run_process(
        self, config: ProcessConfig, future: asyncio.Future[ProcessResult]
    ) -> None:
        """Acquire semaphore(s), launch, monitor, and report result."""
        ext_sem = self._config.external_semaphore
        if ext_sem is not None:
            await ext_sem.acquire()
        await self._await_quota_pause()
        await self._semaphore.acquire()
        host_lease: HostAdmissionLease | None = None
        pending_removed = False
        try:
            host_lease = await self._acquire_host_slot(config)
            self._pending_count -= 1
            pending_removed = True
            try:
                result = await self._launch_and_monitor(config, host_lease=host_lease)
            finally:
                self._release_host_slot(host_lease)
                host_lease = None
        except Exception as exc:
            if not pending_removed:
                self._pending_count = max(0, self._pending_count - 1)
            self._release_host_slot(host_lease)
            self._semaphore.release()
            if ext_sem is not None:
                ext_sem.release()
            if not future.done():
                future.set_exception(exc)
            return
        self._semaphore.release()
        if ext_sem is not None:
            ext_sem.release()
        if not future.done():
            future.set_result(result)

    async def _launch_and_monitor(
        self,
        config: ProcessConfig,
        *,
        host_lease: HostAdmissionLease | None = None,
    ) -> ProcessResult:
        """Launch one process and monitor it until exit or kill."""
        prepared = config.resolve_launch()
        handle = await self._backend.launch(prepared, label=config.label)
        if self._host_admission is not None and host_lease is not None:
            self._host_admission.record_child(
                host_lease,
                child_pid=handle.pid,
                external_id=handle.external_id,
                backend=handle.backend_name,
            )
        key = handle.pid or handle.external_id or id(handle)

        now_mono = time.monotonic()
        active = _ActiveProcess(
            config=config,
            handle=handle,
            prepared=prepared,
            start_time=now_mono,
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            last_log_change_time=now_mono,
        )
        self._active[key] = active
        self._increment_lane_counter(config.lane_id, "active", 1)

        if self._event_logger is not None:
            self._event_logger.process_start(
                handle.pid, handle.external_id, handle.backend_name, config.label
            )
        self._write_status()

        # Poll until exit, checking health on each interval.
        try:
            result = await self._poll_until_exit(active)
        finally:
            self._active.pop(key, None)

        # Record completion.
        self._record_completion(result)
        return result

    async def _acquire_host_slot(self, config: ProcessConfig) -> HostAdmissionLease | None:
        """Acquire host-wide launch admission when configured."""
        if self._host_admission is None:
            return None
        lease = await self._host_admission.acquire(
            label=config.label,
            pool_id=self._pool_id,
            metadata={"backend": self._backend_name},
        )
        if self._event_logger is not None:
            self._event_logger.host_slot_acquired(
                namespace=lease.namespace,
                slot_id=lease.slot_id,
                limit=lease.limit,
                label=lease.label,
                lease_path=str(lease.lease_file),
            )
        return lease

    def _release_host_slot(self, lease: HostAdmissionLease | None) -> None:
        """Release host-wide launch admission when configured."""
        if self._host_admission is None or lease is None:
            return
        self._host_admission.release(lease)
        if self._event_logger is not None:
            self._event_logger.host_slot_released(
                namespace=lease.namespace,
                slot_id=lease.slot_id,
                limit=lease.limit,
                label=lease.label,
                lease_path=str(lease.lease_file),
            )

    async def _poll_until_exit(self, active: _ActiveProcess) -> ProcessResult:
        """Poll the process, checking health periodically."""
        config = active.config
        handle = active.handle
        interval = self._config.monitor_interval_s

        while True:
            # Check if process has exited.
            exit_code = await self._backend.poll(handle)
            if exit_code is not None:
                # Wait for the log filter thread (if any) to flush remaining lines.
                handle.join_filter_thread(timeout=5.0)
                elapsed = time.monotonic() - active.start_time
                return ProcessResult(
                    config=config,
                    pid=handle.pid,
                    external_id=handle.external_id,
                    backend=handle.backend_name,
                    exit_code=exit_code,
                    kill_reason=active.kill_reason,
                    elapsed_s=round(elapsed, 1),
                    peak_rss_bytes=active.peak_rss_bytes or None,
                    peak_descendants=active.peak_descendants or None,
                    log_size_bytes=active.current_log_bytes or None,
                )

            # Health check.
            await self._check_health(active)
            if active.killed:
                # Wait for the process to actually exit after kill.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wait_for_exit(active), timeout=10)
                # Wait for the log filter thread (if any) to flush remaining lines.
                handle.join_filter_thread(timeout=5.0)
                elapsed = time.monotonic() - active.start_time
                return ProcessResult(
                    config=config,
                    pid=handle.pid,
                    external_id=handle.external_id,
                    backend=handle.backend_name,
                    exit_code=None,
                    kill_reason=active.kill_reason,
                    elapsed_s=round(elapsed, 1),
                    peak_rss_bytes=active.peak_rss_bytes or None,
                    peak_descendants=active.peak_descendants or None,
                    log_size_bytes=active.current_log_bytes or None,
                )

            await asyncio.sleep(interval)

    async def _wait_for_exit(self, active: _ActiveProcess) -> None:
        """Wait for a process to exit after being killed."""
        while True:
            exit_code = await self._backend.poll(active.handle)
            if exit_code is not None:
                return
            await asyncio.sleep(0.5)

    async def _check_health(self, active: _ActiveProcess) -> None:
        """Check per-process health limits and kill if violated."""
        config = active.config
        elapsed = time.monotonic() - active.start_time

        # Timeout check.
        if config.timeout_s is not None and elapsed > config.timeout_s:
            log.warning(
                "Process %s exceeded timeout (%.0fs > %.0fs); killing",
                config.label,
                elapsed,
                config.timeout_s,
            )
            active.killed = True
            active.kill_reason = "timeout"
            await self._backend.kill(active.handle)
            return

        # Backend health metrics.
        metrics = await self._backend.health(active.handle)
        if metrics is None:
            # Process may have exited — let the poll loop handle it.
            return

        # Update peaks.
        if metrics.rss_bytes is not None:
            active.current_rss_bytes = metrics.rss_bytes
            active.peak_rss_bytes = max(active.peak_rss_bytes, metrics.rss_bytes)
        if metrics.descendants is not None:
            active.current_descendants = metrics.descendants
            active.peak_descendants = max(active.peak_descendants, metrics.descendants)

        # Log file size (from config's log_path, not from backend).
        log_size = get_log_size(active.prepared.log_path)
        if log_size is not None:
            if log_size > active.last_log_bytes:
                active.last_log_bytes = log_size
                active.last_log_change_time = time.monotonic()
            active.current_log_bytes = log_size

        # Stall detection: kill if log hasn't grown for stall_timeout_s.
        if config.stall_timeout_s is not None:
            stall_duration = time.monotonic() - active.last_log_change_time
            if stall_duration > config.stall_timeout_s:
                log.warning(
                    "Process %s stalled (no log output for %.0fs > %.0fs); killing",
                    config.label,
                    stall_duration,
                    config.stall_timeout_s,
                )
                active.killed = True
                active.kill_reason = "stalled"
                await self._backend.kill(active.handle)
                return

        # RSS limit.
        if (
            config.max_rss_bytes is not None
            and metrics.rss_bytes is not None
            and metrics.rss_bytes > config.max_rss_bytes
        ):
            log.warning(
                "Process %s RSS %d > limit %d; killing",
                config.label,
                metrics.rss_bytes,
                config.max_rss_bytes,
            )
            active.killed = True
            active.kill_reason = "rss_limit"
            await self._backend.kill(active.handle)
            return

        # Log size limit.
        if (
            config.max_log_bytes is not None
            and log_size is not None
            and log_size > config.max_log_bytes
        ):
            log.warning(
                "Process %s log size %d > limit %d; killing",
                config.label,
                log_size,
                config.max_log_bytes,
            )
            active.killed = True
            active.kill_reason = "log_limit"
            await self._backend.kill(active.handle)
            return

        # Descendant limit.
        if (
            config.max_descendants is not None
            and metrics.descendants is not None
            and metrics.descendants > config.max_descendants
        ):
            log.warning(
                "Process %s descendants %d > limit %d; killing",
                config.label,
                metrics.descendants,
                config.max_descendants,
            )
            active.killed = True
            active.kill_reason = "descendants_limit"
            await self._backend.kill(active.handle)
            return

    def _record_completion(self, result: ProcessResult) -> None:
        """Update counters and recent completions."""
        lane_id = result.config.lane_id
        # The matching `+1 to active` happened in _launch_and_monitor; mirror
        # it here so the per-lane active count reflects in-flight work.
        self._increment_lane_counter(lane_id, "active", -1)
        if result.kill_reason is not None:
            self._killed_count += 1
            status = "killed"
            self._increment_lane_counter(lane_id, "killed", 1)
        elif result.exit_code == 0:
            self._completed_count += 1
            status = "completed"
            self._increment_lane_counter(lane_id, "completed", 1)
        else:
            self._failed_count += 1
            status = "failed"
            self._increment_lane_counter(lane_id, "failed", 1)

        # Event log.
        if self._event_logger is not None:
            if result.kill_reason is not None:
                self._event_logger.process_kill(
                    result.pid,
                    result.external_id,
                    result.backend,
                    result.config.label,
                    result.kill_reason,
                    result.peak_rss_bytes,
                )
            else:
                self._event_logger.process_exit(
                    result.pid,
                    result.external_id,
                    result.backend,
                    result.config.label,
                    result.exit_code,
                    result.elapsed_s,
                    result.peak_rss_bytes,
                )

        # Recent completions ring.
        entry = ProcessStatus(
            pid=result.pid,
            external_id=result.external_id,
            backend=result.backend,
            label=result.config.label,
            started_at="",  # We don't have the original start time as ISO
            elapsed_s=result.elapsed_s,
            rss_bytes=0,
            status=status,
            exit_code=result.exit_code,
            kill_reason=result.kill_reason,
            peak_rss_bytes=result.peak_rss_bytes,
            lane_id=lane_id,
            execution_profile=result.config.execution_profile,
        )
        self._recent_completions.append(entry)
        limit = self._config.recent_completions_limit
        if len(self._recent_completions) > limit:
            self._recent_completions = self._recent_completions[-limit:]

        self._write_status()

    # ── Pressure monitor ────────────────────────────────────────

    async def _pressure_monitor_loop(self) -> None:
        """Periodically check memory pressure and adjust concurrency."""

        interval = self._config.pressure_check_interval_s
        while not self._shutdown_event.is_set():
            # Check for external kill sentinel before anything else.
            if self._check_kill_sentinel():
                break

            try:
                # Check for operator overrides before adjustment.
                self._read_overrides()

                health = self._sample_system_health(measure())
                self._latest_health = health
                pressure = health.pressure
                if self._operator_mode_override == "manual":
                    # Manual mode: skip governors, just apply the operator cap.
                    new_cap = self._effective_target()
                    self._set_capacity(new_cap, reason="manual_override")
                else:
                    self._adjust_concurrency(health.level)
                    self._apply_operator_target()

                if self._event_logger is not None:
                    self._event_logger.pressure_check(
                        health.level.value,
                        pressure.available_pct,
                        swap_used_gb=pressure.swap_used_gb,
                        total_memory_gb=pressure.total_memory_gb,
                        memory_level=pressure.level.value,
                        swap_delta_gb_per_min=health.swap_delta_gb_per_min,
                        swap_level=health.swap_level.value,
                        disk_free_gb=health.disk_free_gb,
                        disk_total_gb=health.disk_total_gb,
                        disk_used_pct=health.disk_used_pct,
                        disk_level=health.disk_level.value,
                        disk_pressure_cause=health.disk_pressure_cause,
                        source=health.source,
                        current_concurrency=self._semaphore.capacity,
                        active_count=len(self._active),
                        pending_count=self._pending_count,
                        memory_ceiling=self._memory_ceiling,
                        provider_ceiling=self._provider_ceiling,
                        operator_cap=self._operator_cap,
                        effective_target=self._effective_target(),
                        bottleneck=self._classify_bottleneck(),
                        active_rss_bytes=sum(
                            active.current_rss_bytes for active in self._active.values()
                        )
                        or None,
                        active_peak_rss_bytes=max(
                            (active.peak_rss_bytes for active in self._active.values()),
                            default=0,
                        )
                        or None,
                        active_log_bytes=self._active_log_bytes() or None,
                    )
                if self._health_logger is not None:
                    self._health_logger.health_sample(self._health_payload(health))

                # Refresh status file so external readers see current elapsed_s / health.
                self._write_status()
            except UnsupportedTelemetryPlatformError as exc:
                self._handle_telemetry_failure(exc)
            except Exception:
                log.exception("Error in pressure monitor")

            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=interval)
                break  # Shutdown requested.
            except TimeoutError:
                pass  # Normal — interval elapsed, loop again.

    def _check_kill_sentinel(self) -> bool:
        """Check for an external kill sentinel file and initiate shutdown if found."""
        if self._config.state_dir is None:
            return False
        sentinel_path = self._config.state_dir / POOL_KILL_SENTINEL_FILE
        if not sentinel_path.exists():
            return False

        try:
            kill_req = read_yaml_file(sentinel_path)
            reason = kill_req.get("reason", "unknown") if kill_req else "unknown"
        except Exception:
            log.warning("Failed to read kill sentinel", exc_info=True)
            reason = "unknown"

        log.warning("External kill request detected (reason: %s); shutting down", reason)

        # Mark all active processes so the pool records the correct kill reason.
        for active in self._active.values():
            if not active.killed:
                active.killed = True
                active.kill_reason = "external_kill"

        self._shutdown_event.set()
        return True

    def _read_overrides(self) -> None:
        """Read operator overrides from run-level and pool-local files."""
        paths = self._scale_override_paths
        if not paths:
            self._operator_cap_override = None
            self._operator_mode_override = None
            self._max_concurrency_override = None
            return
        found = False
        operator_cap: int | None = None
        operator_mode: str | None = None
        max_concurrency_override: int | None = None
        try:
            for override_path in paths:
                if not override_path.exists():
                    continue
                found = True
                override = read_scale_override(override_path)
                if override.operator_cap is not None:
                    operator_cap = override.operator_cap
                if override.mode is not None:
                    operator_mode = override.mode
                if override.max_concurrency is not None:
                    max_concurrency_override = override.max_concurrency
            if found:
                self._operator_cap_override = operator_cap
                self._operator_mode_override = operator_mode
                self._max_concurrency_override = max_concurrency_override
            else:
                self._operator_cap_override = None
                self._operator_mode_override = None
                self._max_concurrency_override = None
        except Exception:
            log.warning("Failed to read scale-override.yaml", exc_info=True)

    def _effective_max_concurrency(self) -> int:
        """Effective max_concurrency ceiling for adaptive ramp-up.

        Returns the operator-written ``max_concurrency`` override if set, else
        the launch-time ``self._config.max_concurrency``. Used as the upper
        bound when the memory and provider governors ramp ceilings UP under
        normal pressure. Setting this override lets the controller lift the
        ramp ceiling live without an orchestrator kill+restart.

        See logbook the incident analysis (live-validated: lifting
        the launch envelope lets the controller scale 8 → 19 effective in
        ~15 min when pressure stays normal).
        """
        override = self._max_concurrency_override
        if override is not None and override > self._config.max_concurrency:
            return override
        return self._config.max_concurrency

    def _health_disk_path(self) -> Path:
        """Return the filesystem path whose free space limits this pool."""
        if self._config.logs_dir is not None:
            return self._config.logs_dir
        if self._config.state_dir is not None:
            return self._config.state_dir
        return Path.cwd()

    def _active_log_bytes(self) -> int:
        """Return known active-process log bytes."""
        return sum(active.current_log_bytes for active in self._active.values())

    def _sample_system_health(self, pressure: MemoryPressure) -> SystemHealthSample:
        """Build one normalized system-health sample for status and logs."""
        disk_usage = shutil.disk_usage(self._health_disk_path())
        disk_total_gb = disk_usage.total / (1024**3)
        disk_free_gb = disk_usage.free / (1024**3)
        disk_used_pct = (
            (disk_usage.total - disk_usage.free) / disk_usage.total * 100
            if disk_usage.total > 0
            else 100.0
        )

        now = time.monotonic()
        swap_delta_gb_per_min = 0.0
        if self._last_swap_sample is not None:
            previous_ts, previous_swap_gb = self._last_swap_sample
            elapsed_min = (now - previous_ts) / 60
            if elapsed_min > 0:
                swap_delta_gb_per_min = max(
                    0.0,
                    (pressure.swap_used_gb - previous_swap_gb) / elapsed_min,
                )
        self._last_swap_sample = (now, pressure.swap_used_gb)

        swap_level = classify_swap_rate(
            swap_delta_gb_per_min,
            total_memory_gb=pressure.total_memory_gb,
        )
        disk_level = _classify_disk_free(disk_free_gb)
        active_log_bytes = self._active_log_bytes()
        disk_pressure_cause = _classify_disk_pressure_cause(
            pressure=pressure,
            disk_level=disk_level,
            swap_level=swap_level,
            active_log_bytes=active_log_bytes,
        )
        return SystemHealthSample(
            pressure=pressure,
            disk_free_gb=disk_free_gb,
            disk_total_gb=disk_total_gb,
            disk_used_pct=disk_used_pct,
            disk_level=disk_level,
            disk_pressure_cause=disk_pressure_cause,
            swap_delta_gb_per_min=swap_delta_gb_per_min,
            swap_level=swap_level,
        )

    def _handle_telemetry_failure(self, exc: UnsupportedTelemetryPlatformError) -> None:
        """Make runtime telemetry failures visible and reduce launch pressure."""
        # Callers reach this from inside an `except ... as exc` block, so the ambient
        # exception state is set; pass it explicitly so the traceback does not depend
        # on that and the intent is visible at this call site.
        log.error(
            "RunPool resource telemetry failed; reducing concurrency to minimum",
            exc_info=exc,
        )
        self._memory_ceiling = self._config.min_concurrency
        self._set_capacity(self._effective_target(), reason="telemetry_unavailable")
        if self._event_logger is not None:
            self._event_logger.resource_telemetry_error(
                str(exc),
                action="reduced_to_min_concurrency",
                current_concurrency=self._semaphore.capacity,
                effective_target=self._effective_target(),
            )
        if self._latest_health is not None:
            self._write_status()

    def _health_payload(
        self,
        health: SystemHealthSample,
    ) -> dict[str, object]:
        """Return the structured JSONL payload for one health sample."""
        active_rss_bytes = sum(active.current_rss_bytes for active in self._active.values()) or 0
        active_peak_rss_bytes = (
            max((active.peak_rss_bytes for active in self._active.values()), default=0) or 0
        )
        payload: dict[str, object] = {
            "level": health.level.value,
            "memory_level": health.pressure.level.value,
            "available_pct": round(health.pressure.available_pct, 1),
            "swap_used_gb": round(health.pressure.swap_used_gb, 2),
            "total_memory_gb": round(health.pressure.total_memory_gb, 2),
            "swap_delta_gb_per_min": round(health.swap_delta_gb_per_min, 2),
            "swap_level": health.swap_level.value,
            "disk_free_gb": round(health.disk_free_gb, 2),
            "disk_total_gb": round(health.disk_total_gb, 2),
            "disk_used_pct": round(health.disk_used_pct, 1),
            "disk_level": health.disk_level.value,
            "disk_pressure_cause": health.disk_pressure_cause,
            "source": health.source,
            "current_concurrency": self._semaphore.capacity,
            "active_count": len(self._active),
            "pending_count": self._pending_count,
            "memory_ceiling": self._memory_ceiling,
            "provider_ceiling": self._provider_ceiling,
            "operator_cap": self._operator_cap,
            "effective_target": self._effective_target(),
            "bottleneck": self._classify_bottleneck(),
            "active_rss_bytes": active_rss_bytes,
            "active_peak_rss_bytes": active_peak_rss_bytes,
            "active_log_bytes": self._active_log_bytes(),
        }
        return payload

    def _prune_recent_rate_limits(self, now: float | None = None) -> None:
        """Drop rate-limit samples that have aged out of the burst window."""
        if now is None:
            now = time.monotonic()
        window_start = now - self._config.rate_limit_burst_window_s
        while self._recent_rate_limits and self._recent_rate_limits[0] < window_start:
            self._recent_rate_limits.popleft()

    @property
    def _operator_cap(self) -> int:
        """Effective operator cap — override if set, else config max_concurrency."""
        if self._operator_cap_override is not None:
            return self._operator_cap_override
        return self._config.max_concurrency

    def _effective_target(self) -> int:
        """Current effective cap after memory/provider/operator ceilings."""
        return max(
            self._config.min_concurrency,
            min(
                self._memory_ceiling,
                self._provider_ceiling,
                self._operator_cap,
            ),
        )

    def _resource_bottleneck_label(self) -> str:
        """Return the resource signal currently limiting launch concurrency."""
        health = self._latest_health
        if health is None:
            return "memory-bound"

        labels: list[str] = []
        if health.pressure.level != PressureLevel.NORMAL:
            labels.append("memory-bound")
        if health.swap_level != PressureLevel.NORMAL:
            labels.append("swap-rate-bound")
        if health.disk_level != PressureLevel.NORMAL:
            disk_label = "disk-bound"
            if health.disk_pressure_cause != "none":
                disk_label += f":{health.disk_pressure_cause}"
            labels.append(disk_label)
        return "+".join(labels) or "resource-bound"

    def _classify_bottleneck(self) -> str:
        """Return the most restrictive live ceiling."""
        operator_cap = self._operator_cap
        max_cap = self._config.max_concurrency
        resource_label = self._resource_bottleneck_label()
        # Nothing is capping — all ceilings are at or above max_concurrency.
        if (
            operator_cap >= max_cap
            and self._memory_ceiling >= max_cap
            and self._provider_ceiling >= max_cap
        ):
            return "uncapped"
        if self._provider_ceiling < operator_cap and self._provider_ceiling < self._memory_ceiling:
            return "DSQ-bound"
        if self._memory_ceiling < operator_cap and self._memory_ceiling < self._provider_ceiling:
            return resource_label
        if self._provider_ceiling < operator_cap and self._provider_ceiling == self._memory_ceiling:
            return f"DSQ-bound+{resource_label}"
        return "operator-capped"

    def _set_capacity(self, new_cap: int, *, reason: str) -> None:
        """Apply a new pool cap and emit the standard adjustment event."""
        old_cap = self._semaphore.capacity
        if new_cap == old_cap:
            return
        self._semaphore.set_capacity(new_cap)
        log.info(
            "Concurrency adjusted: %d → %d (reason=%s, memory_ceiling=%d, provider_ceiling=%d)",
            old_cap,
            new_cap,
            reason,
            self._memory_ceiling,
            self._provider_ceiling,
        )
        if self._event_logger is not None:
            self._event_logger.concurrency_adjust(
                old_cap,
                new_cap,
                reason,
                self._consecutive_normal,
                self._consecutive_elevated,
                memory_ceiling=self._memory_ceiling,
                provider_ceiling=self._provider_ceiling,
                operator_cap=self._operator_cap,
                effective_target=self._effective_target(),
                bottleneck=self._classify_bottleneck(),
            )

    def _apply_operator_target(self) -> None:
        """Apply an explicit operator cap change during adaptive mode."""
        if self._operator_cap_override is None:
            return
        target = self._effective_target()
        if self._semaphore.capacity != target:
            self._set_capacity(target, reason="operator_override")

    def _adjust_concurrency(self, level: PressureLevel) -> None:
        """Adjust semaphore capacity based on memory pressure.

        See ``RunPoolConfig`` docstring for the full policy table.
        NORMAL and ELEVATED use hysteresis (wait ``hysteresis_checks``
        consecutive readings before acting); HIGH and CRITICAL act
        immediately every tick.
        """
        memory_changed = False
        provider_changed = False

        if level == PressureLevel.NORMAL:
            if len(self._active) > self._semaphore.capacity:
                self._consecutive_normal = 0
            else:
                self._consecutive_normal += 1
            self._consecutive_elevated = 0
            effective_max = self._effective_max_concurrency()
            if (
                self._consecutive_normal >= self._config.hysteresis_checks
                and self._memory_ceiling < effective_max
            ):
                increment = max(1, int(self._memory_ceiling * self._config.ramp_up_factor))
                self._memory_ceiling = min(
                    self._memory_ceiling + increment,
                    effective_max,
                )
                self._consecutive_normal = 0
                memory_changed = True
        elif level == PressureLevel.ELEVATED:
            self._consecutive_normal = 0
            self._consecutive_elevated += 1
            # ELEVATED is a HOLD signal — match the docstring on
            # PressureLevel.ELEVATED ("20-40% — hold current concurrency").
            # The previous code reduced 10% per hysteresis cycle, which under
            # SUSTAINED elevated pressure (e.g. two parallel dispatches
            # sharing one laptop, with available_pct hovering at 40-46%)
            # ratchets all the way to min_concurrency over a few minutes
            # — even though pressure isn't actually worsening. Reductions
            # now only fire at HIGH / CRITICAL where memory is genuinely
            # under threat. 2026-05-13 production rerun: a tech smoke at sustained
            # ELEVATED dropped 13→1 in 5 minutes.
        elif level == PressureLevel.HIGH:
            self._consecutive_normal = 0
            self._consecutive_elevated = 0
            decrement = max(1, math.ceil(self._memory_ceiling * self._config.ramp_down_factor))
            self._memory_ceiling = max(
                self._memory_ceiling - decrement,
                self._config.min_concurrency,
            )
            memory_changed = True
        elif level == PressureLevel.CRITICAL:
            self._consecutive_normal = 0
            self._consecutive_elevated = 0
            decrement = max(1, math.ceil(self._memory_ceiling * self._config.critical_down_factor))
            self._memory_ceiling = max(
                self._memory_ceiling - decrement,
                self._config.min_concurrency,
            )
            memory_changed = True

        # Provider governor — runs independently of memory pressure.
        now = time.monotonic()
        self._prune_recent_rate_limits(now)
        retries_growing = self._pending_retries > self._prev_pending_retries
        self._prev_pending_retries = self._pending_retries
        if retries_growing or self._recent_rate_limits:
            # Retries growing or recent rate limits in burst window — not clear.
            self._consecutive_provider_clear = 0
        else:
            self._consecutive_provider_clear += 1
            effective_max = self._effective_max_concurrency()
            if (
                self._consecutive_provider_clear >= self._config.hysteresis_checks
                and self._provider_ceiling < effective_max
            ):
                increment = max(1, int(self._provider_ceiling * self._config.ramp_up_factor))
                self._provider_ceiling = min(
                    self._provider_ceiling + increment,
                    effective_max,
                )
                self._consecutive_provider_clear = 0
                provider_changed = True

        if provider_changed and not memory_changed:
            reason = "provider_recovery"
        elif memory_changed and not provider_changed:
            reason = f"pressure_{level.value}"
        elif provider_changed or memory_changed:
            reason = f"pressure_{level.value}+provider_recovery"
        else:
            reason = None

        if reason is not None:
            self._set_capacity(self._effective_target(), reason=reason)

    # ── Status snapshot ─────────────────────────────────���───────

    def _build_status(self) -> RunPoolStatus:
        """Build a RunPoolStatus from current state."""

        now = datetime.now(UTC).isoformat(timespec="seconds")
        self._prune_recent_rate_limits()
        health = self._latest_health
        if health is None:
            health = self._sample_system_health(measure())
            self._latest_health = health
        pressure = health.pressure
        pressure_status = PressureStatus(
            level=health.level,
            available_pct=pressure.available_pct,
            swap_used_gb=pressure.swap_used_gb,
            total_memory_gb=pressure.total_memory_gb,
            swap_delta_gb_per_min=health.swap_delta_gb_per_min,
            swap_level=health.swap_level,
            disk_free_gb=health.disk_free_gb,
            disk_total_gb=health.disk_total_gb,
            disk_used_pct=health.disk_used_pct,
            disk_level=health.disk_level,
            disk_pressure_cause=health.disk_pressure_cause,
            source=health.source,
        )

        processes = []
        for active in self._active.values():
            elapsed = time.monotonic() - active.start_time
            processes.append(
                ProcessStatus(
                    pid=active.handle.pid,
                    external_id=active.handle.external_id,
                    backend=active.handle.backend_name,
                    label=active.config.label,
                    started_at=active.started_at,
                    elapsed_s=round(elapsed, 1),
                    rss_bytes=active.current_rss_bytes or None,
                    descendants=active.current_descendants or None,
                    log_bytes=active.current_log_bytes or None,
                    status="running",
                    lane_id=active.config.lane_id,
                    execution_profile=active.config.execution_profile,
                )
            )

        lane_statuses = self._build_lane_statuses()

        fc = self._failure_class_counts
        failure_counts = FailureCounts(
            rate_limited=fc.get("rate_limited", 0),
            server_error=fc.get("server_error", 0),
            timeout=fc.get("timeout", 0),
            invalid_output=fc.get("invalid_output", 0),
            crash=fc.get("crash", 0),
            unknown=fc.get("unknown", 0),
        )
        controller = ControllerStatus(
            mode=self._operator_mode_override or "adaptive",
            operator_cap=self._operator_cap,
            effective_target=self._effective_target(),
            memory_ceiling=self._memory_ceiling,
            provider_ceiling=self._provider_ceiling,
            bottleneck=self._classify_bottleneck(),
            recent_rate_limits=len(self._recent_rate_limits),
            pending_retries=self._pending_retries,
        )

        return RunPoolStatus(
            pool_id=self._pool_id,
            pid=os.getpid(),
            started_at=self._started_at,
            updated_at=now,
            backend=self._backend_name,
            max_concurrency=self._config.max_concurrency,
            current_concurrency=self._semaphore.capacity,
            active_count=len(self._active),
            pending_count=self._pending_count,
            completed_count=self._completed_count,
            failed_count=self._failed_count,
            killed_count=self._killed_count,
            pending_retries=self._pending_retries,
            pressure=pressure_status,
            failure_counts=failure_counts,
            controller=controller,
            concurrency_plan=self._concurrency_plan,
            processes=processes,
            recent_completions=list(self._recent_completions),
            lanes=lane_statuses,
        )

    def _build_lane_statuses(self) -> list[LaneStatus]:
        """Render the registered lanes (and their counters) as status rows."""
        rows: list[LaneStatus] = []
        for lane_id, lane in self._lane_registry.items():
            counters = self._lane_counters.get(
                lane_id, {"active": 0, "completed": 0, "failed": 0, "killed": 0}
            )
            rows.append(
                LaneStatus(
                    lane_id=lane_id,
                    execution_profile=lane.execution_profile,
                    adapter=lane.adapter,
                    replica_index=lane.replica_index,
                    comparison_role=lane.comparison_role,
                    active_count=counters["active"],
                    completed_count=counters["completed"],
                    failed_count=counters["failed"],
                    killed_count=counters["killed"],
                )
            )
        return rows

    def _increment_lane_counter(self, lane_id: str | None, key: str, delta: int) -> None:
        """Bump a per-lane counter, registering unknown lane ids lazily.

        Tasks may be submitted with lane ids that weren't registered at
        construction time (e.g. when an orchestrator picks a lane outside
        the declared matrix). Lazy registration keeps the status output
        coherent without crashing the pool over a misconfigured task.
        """
        if lane_id is None:
            return
        if lane_id not in self._lane_counters:
            self._lane_registry.setdefault(
                lane_id,
                ExecutionLane(
                    lane_id=lane_id,
                    execution_profile=lane_id,
                ),
            )
            self._lane_counters[lane_id] = {
                "active": 0,
                "completed": 0,
                "failed": 0,
                "killed": 0,
            }
        counters = self._lane_counters[lane_id]
        counters[key] = max(0, counters.get(key, 0) + delta)

    def _write_status(self) -> None:
        """Write current status to the YAML file (if configured)."""
        if self._status_path is None:
            return
        try:
            status = self._build_status()
            write_status(self._status_path, status)
            if self._scale_state_path is not None and status.controller is not None:
                write_scale_state(
                    self._scale_state_path,
                    ScaleState(updated_at=status.updated_at, controller=status.controller),
                )
        except Exception:
            log.exception("Failed to write pool status file")
