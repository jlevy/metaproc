"""Pool status snapshot — Pydantic models and atomic YAML writer.

The status file (``runpool-status.yaml``) is the pool's single source of
external truth.  Every state mutation triggers an atomic rewrite so readers
never see partial state.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

import psutil
from pydantic import BaseModel, Field

from metaproc.io import atomic_output_file, new_yaml, read_yaml_file
from metaproc.osutils.memory_pressure import PressureLevel
from metaproc.runpool.concurrency import ConcurrencyPlan


# Operator-readable status YAML keeps explicit `null` / `{}` values for
# fields that are intentionally unset (e.g., `cli_max_concurrency: null`),
# so suppress_vals is disabled. to_yaml_string would otherwise drop them.
# ruamel.yaml's add_representer mutates a class-level dispatch table, so the
# YAML handle is rebuilt per-call to avoid pollution from concurrent
# to_yaml_string callers in the same process.
def _dump_status_yaml(value: Any) -> str:
    buf = io.StringIO()
    new_yaml(suppress_vals=lambda _v: False).dump(value, buf)
    return buf.getvalue()


class PressureStatus(BaseModel):
    """Serialized pressure snapshot nested inside RunPoolStatus."""

    level: PressureLevel
    available_pct: float
    swap_used_gb: float
    # Current writers always populate this field from required OS telemetry.
    # Older run artifacts created before the field existed are still readable so
    # operator/status commands can resume or inspect interrupted runs.
    total_memory_gb: float | None = None
    swap_delta_gb_per_min: float = 0.0
    swap_level: PressureLevel = PressureLevel.NORMAL
    disk_free_gb: float | None = None
    disk_total_gb: float | None = None
    disk_used_pct: float | None = None
    disk_level: PressureLevel = PressureLevel.NORMAL
    disk_pressure_cause: str = "none"
    source: str


class ProcessStatus(BaseModel):
    """Status of a single managed process, serialized in runpool-status.yaml."""

    pid: int | None = None
    external_id: str | None = None
    backend: str
    label: str
    started_at: str
    elapsed_s: float
    rss_bytes: int | None = None
    descendants: int | None = None
    log_bytes: int | None = None
    status: str  # "running", "completed", "failed", "killed"
    exit_code: int | None = None
    kill_reason: str | None = None
    peak_rss_bytes: int | None = None
    lane_id: str | None = None
    execution_profile: str | None = None


class LaneStatus(BaseModel):
    """Per-lane aggregated counters for an execution lane.

    Emitted by lane-aware pools so operators can see at a glance how each
    lane in a comparison runpool is faring relative to others (e.g. one
    profile degrading, another healthy). The degenerate single-lane case
    still emits one entry so consumers have a uniform reading path.
    """

    lane_id: str
    execution_profile: str | None = None
    adapter: str | None = None
    replica_index: int = 0
    comparison_role: str | None = None
    active_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    killed_count: int = 0


class FailureCounts(BaseModel):
    """Counts of failures by category, for rate-limit visibility."""

    rate_limited: int = 0
    server_error: int = 0
    timeout: int = 0
    invalid_output: int = 0
    crash: int = 0
    unknown: int = 0


class ControllerStatus(BaseModel):
    """Current adaptive-controller state for operator visibility."""

    mode: str = "adaptive"
    operator_cap: int
    effective_target: int
    memory_ceiling: int
    provider_ceiling: int
    bottleneck: str
    recent_rate_limits: int = 0
    pending_retries: int = 0


class ScaleBounds(BaseModel):
    """Safety bounds for operator-driven topology changes."""

    min_workers: int = 1
    max_workers: int | None = None
    min_concurrency: int = 1
    max_concurrency: int | None = None


class RunPoolStatus(BaseModel):
    """Full pool snapshot.  Written atomically to runpool-status.yaml."""

    pool_id: str
    pid: int
    started_at: str
    updated_at: str
    backend: str
    max_concurrency: int
    current_concurrency: int
    active_count: int
    pending_count: int
    completed_count: int
    failed_count: int
    killed_count: int
    pending_retries: int = 0
    pressure: PressureStatus
    failure_counts: FailureCounts = Field(default_factory=FailureCounts)
    controller: ControllerStatus | None = None
    concurrency_plan: ConcurrencyPlan | None = None
    processes: list[ProcessStatus] = Field(default_factory=list)
    recent_completions: list[ProcessStatus] = Field(default_factory=list)
    lanes: list[LaneStatus] = Field(default_factory=list)


class ScaleState(BaseModel):
    """Controller state persisted separately for reconnect and future overrides."""

    updated_at: str
    controller: ControllerStatus
    desired_workers: int | None = None
    desired_max_concurrency: int | None = None
    generation: int = 0
    bounds: ScaleBounds | None = None


class ScaleOverride(BaseModel):
    """Operator-written overrides consumed by the controller on each tick.

    The operator creates ``scale-override.yaml`` in the pool state directory.
    The controller reads it on each pressure tick and adopts any non-null
    fields.  The controller never writes this file — the operator owns it.

    Fields
    ------
    mode : ``"manual"`` freezes both governors; ``"adaptive"`` (or absent)
        resumes normal dual-governor control.
    operator_cap : explicit concurrency cap that replaces ``max_concurrency``
        in the effective-target calculation.  Cleared by removing the file or
        setting the field to ``null``.  Bounded by launch-time ``max_concurrency``
        — to raise effective concurrency *above* the launch envelope, use the
        ``max_concurrency`` field instead.
    max_concurrency : explicit launch-envelope override that lifts the
        ``self._config.max_concurrency`` ceiling used by the adaptive
        memory/provider governors on their ramp-up paths.  Setting this lets
        the controller ramp memory_ceiling and provider_ceiling above the
        launch-time max as long as actual memory + provider pressure stays
        normal.  Cleared by removing the file or setting to ``null``.
        See logbook the incident analysis for the live-validated
        rationale and the spec
        the orchestrator resilience design
        for design notes.
    """

    mode: str | None = None
    operator_cap: int | None = None
    max_concurrency: int | None = None


# ── File I/O ────────────────────────────────────────────────────


def write_status(path: Path, status: RunPoolStatus) -> None:
    """Atomically write the pool status to a YAML file.

    Uses ``atomic_output_file`` so readers never see partial content.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = status.model_dump(mode="json")
    content = _dump_status_yaml(data)

    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(content)
        # Promote to 0o644 so non-owner operators on the same host
        # (e.g., browser VM readers when the worker ran as root) can
        # read the status file.
        os.chmod(tmp, 0o644)


def read_status(path: Path) -> RunPoolStatus:
    """Parse an existing status file."""
    data = read_yaml_file(path)
    return RunPoolStatus.model_validate(data)


def write_scale_state(path: Path, scale_state: ScaleState) -> None:
    """Atomically write the adaptive controller state to YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = scale_state.model_dump(mode="json")
    content = _dump_status_yaml(data)

    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(content)
        os.chmod(tmp, 0o644)


def read_scale_state(path: Path) -> ScaleState:
    """Parse an existing scale-state file.

    Raises ``ValueError`` if the file is empty or contains no document — the
    caller should treat a missing/empty file as "no prior state".
    """
    data = read_yaml_file(path)
    if data is None:
        raise ValueError(f"scale-state file is empty or malformed: {path}")
    return ScaleState.model_validate(data)


def read_scale_override(path: Path) -> ScaleOverride:
    """Parse an operator-written scale-override file."""
    data = read_yaml_file(path)
    if data is None:
        return ScaleOverride()
    return ScaleOverride.model_validate(data)


def write_scale_override(path: Path, scale_override: ScaleOverride) -> None:
    """Atomically write an operator scale-override file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = scale_override.model_dump(mode="json", exclude_none=True)
    content = _dump_status_yaml(data)

    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(content)
        os.chmod(tmp, 0o644)


def is_pool_alive(status: RunPoolStatus) -> bool:
    """Check if the pool process is still running."""
    try:
        proc = psutil.Process(status.pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
