"""Run status scanning and aggregation.

Provides the core Python API for ``metaproc status`` and ``metaproc wait``.
All scanning, aggregation, and timing logic lives here — no CLI concerns,
no formatting. Returns Pydantic models for callers to format, serialize, or check.
"""

from __future__ import annotations

import contextlib
import logging
import re
import statistics
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from ruamel.yaml import YAMLError

from metaproc.engine.dep_state import (
    compute_step_state,
    fingerprint_step,
    recorded_step_hash,
)
from metaproc.io import read_yaml_file
from metaproc.io.orchestrator_lease import is_orchestrator_alive
from metaproc.io.state_io import read_status_at
from metaproc.models.authored import ProgressCounts
from metaproc.models.plan import Plan, ResolvedStep
from metaproc.models.runtime import StatusRecord, StepState
from metaproc.models.viz import NodeProgress, ProgressSnapshot
from metaproc.osutils.memory_pressure import measure as measure_pressure
from metaproc.paths import POOL_STATUS_FILE, STATE_DIR, STATUS_FILE

log = logging.getLogger(__name__)

_ISO_FMT = "%Y-%m-%dT%H:%M:%S"


# ── Data models ──────────────────────────────────────────────────


class TimingStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    avg_seconds: float
    min_seconds: float
    max_seconds: float
    p50_seconds: float | None = None
    p95_seconds: float | None = None
    p99_seconds: float | None = None
    elapsed: timedelta | None = None
    eta_seconds: float | None = None


class FailedItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    item: str
    error: str
    attempt: int


class RetryingItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    item: str
    attempt: int
    max_retries: int


class VariantStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    variant: str
    counts: ProgressCounts
    timing: TimingStats | None = None
    failed_items: list[FailedItem] = []
    retrying_items: list[RetryingItem] = []


class SystemMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_available_pct: float
    pressure_level: str
    swap_used_gb: float
    subprocess_count: int
    rss_bytes: int


class StepStatusEntry(BaseModel):
    """Per-step state row surfaced by ``metaproc status``."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    state: StepState
    recorded_hash: str | None = None
    current_hash: str | None = None
    item_counts: dict[str, int] | None = None
    reason: str | None = None


class RunStatus(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    run_dir: Path
    started_at: datetime | None = None
    elapsed: timedelta | None = None
    is_active: bool
    variants: list[VariantStatus] = []
    totals: ProgressCounts
    pending_retries: int = 0
    system: SystemMetrics | None = None
    steps: list[StepStatusEntry] = []
    # "current" if every step is current/missing; "stale" if any step is
    # stale or invalidated; None when the plan could not be loaded.
    process_state: Literal["current", "stale"] | None = None
    # Terminal/runtime state from .state/process-status.yaml. This is
    # intentionally separate from process_state, which describes whether
    # the current process definition matches prior completed work.
    process_execution_state: Literal["running", "completed", "failed", "cancelled"] | None = None
    process_error: str | None = None
    # Activity sub-flags. ``items_running`` is True iff any fan-out item
    # is currently in-flight; ``orchestrator_alive`` is True iff a
    # parent run-process / composite engine still holds its lease.
    # ``is_active`` is the disjunction of these two plus pending-retry
    # state; exposing the inputs lets callers distinguish "items
    # running" from "between steps" from "fully terminal", including when
    # the variant table reads 100 percent but the next step has been queued.
    items_running: bool = False
    orchestrator_alive: bool = False


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    exit_code: int
    reason: str


# ── Scanning ─────────────────────────────────────────────────────


def scan_variant_states(variant_dir: Path) -> list[StatusRecord]:
    """Per-task state under a step's new-layout state root.

    Compatibility shim: *variant_dir* is now interpreted as the step-scoped
    task state root (``<run>/.state/tasks/<step_id>/``). Each immediate
    subdirectory is one item state dir with ``status.yaml`` directly inside.
    """
    results: list[StatusRecord] = []
    if not variant_dir.exists():
        return results
    for item_state in sorted(variant_dir.iterdir()):
        if not item_state.is_dir():
            continue
        record = read_status_at(item_state)
        if record is not None:
            results.append(record)
    return results


def detect_variants(run_dir: Path) -> list[Path]:
    """Find step state roots — any subdir of <run>/.state/tasks/ with status.yaml files."""
    tasks_root = run_dir / STATE_DIR / "tasks"
    if not tasks_root.exists():
        return []
    variants: list[Path] = []
    for candidate in sorted(tasks_root.iterdir()):
        if not candidate.is_dir():
            continue
        has_items = any(
            (item / STATUS_FILE).exists() for item in candidate.iterdir() if item.is_dir()
        )
        if has_items:
            variants.append(candidate)
    return variants


# ── Aggregation ──────────────────────────────────────────────────


def compute_progress(statuses: list[StatusRecord], total: int | None) -> ProgressCounts:
    """Aggregate status records into progress counts."""
    completed = sum(1 for s in statuses if s.state == "completed")
    running = sum(1 for s in statuses if s.state == "running")
    failed = sum(1 for s in statuses if s.state == "failed")
    cached = sum(1 for s in statuses if s.state == "cached")
    retrying = sum(1 for s in statuses if s.state == "running" and s.attempt > 1)

    scanned = completed + running + failed + cached
    effective_total = total if total is not None else scanned
    pending = max(0, effective_total - scanned)

    return ProgressCounts(
        total=effective_total,
        pending=pending,
        running=running,
        completed=completed,
        failed=failed,
        cached=cached,
        retrying=retrying,
    )


def compute_timing(statuses: list[StatusRecord]) -> TimingStats | None:
    """Compute timing statistics from completed items' timestamps."""
    durations: list[float] = []
    earliest_start: datetime | None = None

    for s in statuses:
        if s.state not in ("completed", "cached"):
            continue
        if not s.started_at or not s.completed_at:
            continue
        try:
            start = datetime.strptime(s.started_at, _ISO_FMT)
            end = datetime.strptime(s.completed_at, _ISO_FMT)
        except ValueError:
            continue
        duration = (end - start).total_seconds()
        if duration >= 0:
            durations.append(duration)
        if earliest_start is None or start < earliest_start:
            earliest_start = start

    if not durations:
        return None

    avg = sum(durations) / len(durations)
    elapsed = datetime.now(tz=UTC) - earliest_start.replace(tzinfo=UTC) if earliest_start else None

    # Percentiles (need at least 2 data points for quantiles)
    p50 = None
    p95 = None
    p99 = None
    if len(durations) >= 2:
        sorted_d = sorted(durations)
        q = statistics.quantiles(sorted_d, n=100)
        p50 = q[49]  # 50th percentile
        p95 = q[94]  # 95th percentile
        p99 = q[98]  # 99th percentile

    return TimingStats(
        avg_seconds=avg,
        min_seconds=min(durations),
        max_seconds=max(durations),
        p50_seconds=p50,
        p95_seconds=p95,
        p99_seconds=p99,
        elapsed=elapsed,
        eta_seconds=None,  # Computed by caller with knowledge of remaining items
    )


# ── Item extraction ──────────────────────────────────────────────


def _item_name(record: StatusRecord) -> str:
    """Extract a display name from the item dict (first value)."""
    if record.item:
        return next(iter(record.item.values()))
    return "unknown"


def _extract_failed_items(statuses: list[StatusRecord]) -> list[FailedItem]:
    return [
        FailedItem(
            item=_item_name(s),
            error=s.error or "unknown error",
            attempt=s.attempt,
        )
        for s in statuses
        if s.state == "failed"
    ]


def _extract_retrying_items(statuses: list[StatusRecord]) -> list[RetryingItem]:
    return [
        RetryingItem(
            item=_item_name(s),
            attempt=s.attempt,
            max_retries=0,  # Not available from status.yaml alone
        )
        for s in statuses
        if s.state == "running" and s.attempt > 1
    ]


# ── Items total ──────────────────────────────────────────────────

_PROGRESS_FILENAME = "progress.md"


def read_items_total(run_dir: Path) -> int | None:
    """Read progress.md in the run directory and return the total item count.

    Looks for progress.md in run_dir and its parent (to handle variant-level
    vs run-level paths). Returns None if no progress file is found.
    """
    for candidate in [run_dir / _PROGRESS_FILENAME, run_dir.parent / _PROGRESS_FILENAME]:
        if not candidate.exists():
            continue
        try:
            from metaproc.io import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
                fmf_read_frontmatter,
            )

            raw = fmf_read_frontmatter(candidate)
            if isinstance(raw, dict):
                progress = raw.get("progress", {})
                if isinstance(progress, dict):
                    items = progress.get("items", [])
                    if isinstance(items, list):
                        return len(items)
        except Exception:
            log.warning("Failed to read items file from %s", candidate, exc_info=True)
    return None


# ── Subprocess metrics ───────────────────────────────────────────

# Process name patterns for agent subprocesses launched by metaproc
_AGENT_PROCESS_PATTERNS = re.compile(r"pi-cli|claude|gemini-cli")


def _measure_subprocesses() -> tuple[int, int]:
    """Count agent subprocesses and their aggregate RSS.

    Returns (count, rss_bytes). Uses ``ps`` to avoid a psutil dependency.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "rss,comm"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return 0, 0

        count = 0
        total_rss_kb = 0
        for line in result.stdout.splitlines()[1:]:  # skip header
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            rss_str, comm = parts
            if _AGENT_PROCESS_PATTERNS.search(comm):
                count += 1
                with contextlib.suppress(ValueError):
                    total_rss_kb += int(rss_str)
        return count, total_rss_kb * 1024  # convert KB to bytes
    except (subprocess.TimeoutExpired, OSError):
        return 0, 0


# ── Main entry point ─────────────────────────────────────────────


def scan_run_status(
    run_dir: Path,
    *,
    variant: str | None = None,
    include_system: bool = True,
    plan: Plan | None = None,
) -> RunStatus:
    """Scan a run directory and return aggregated status.

    This is the main entry point for programmatic consumers.

    When *plan* is supplied, the returned status carries a ``steps``
    array — one ``StepStatusEntry`` per plan step, populated via
    ``compute_step_state`` — plus a one-line ``process_state`` summary
    ("current" or "stale"). When *plan* is None the steps section is
    empty and ``process_state`` stays None; callers pass it explicitly
    when they have the resolved plan in hand (e.g. the ``status`` CLI
    loads it from ``run-config.yaml``).
    """
    variant_dirs = detect_variants(run_dir)

    if variant:
        variant_dirs = [v for v in variant_dirs if v.name == variant]

    # Read items total from progress.md for accurate pending counts
    items_total = read_items_total(run_dir)

    variant_statuses: list[VariantStatus] = []
    all_statuses: list[StatusRecord] = []
    any_running = False

    # Per-variant total: if we know items total, each variant gets the same total
    # (each variant processes the full items file).
    per_variant_total = items_total

    for vdir in variant_dirs:
        statuses = scan_variant_states(vdir)
        all_statuses.extend(statuses)

        counts = compute_progress(statuses, total=per_variant_total)
        timing = compute_timing(statuses)
        failed = _extract_failed_items(statuses)
        retrying = _extract_retrying_items(statuses)

        if counts.running > 0:
            any_running = True

        variant_statuses.append(
            VariantStatus(
                variant=vdir.name,
                counts=counts,
                timing=timing,
                failed_items=failed,
                retrying_items=retrying,
            )
        )

    # Overall totals: if items_total known, multiply by number of variants
    overall_total = items_total * len(variant_statuses) if items_total is not None else None
    totals = compute_progress(all_statuses, total=overall_total)

    # Determine earliest start
    started_at: datetime | None = None
    for s in all_statuses:
        if s.started_at:
            try:
                t = datetime.strptime(s.started_at, _ISO_FMT)
                if started_at is None or t < started_at:
                    started_at = t
            except ValueError:
                continue

    elapsed = datetime.now(tz=UTC) - started_at.replace(tzinfo=UTC) if started_at else None

    # Pool-level pending retries (from runpool-status.yaml).
    # A root-level run-parallel writes to {run_dir}/.state/runpool-status.yaml.
    # A run-process fan-out step writes to
    # {run_dir}/.state/steps/{step_id}/runpool-status.yaml.
    pending_retries = 0
    pool_alive = False

    pool_status_candidates = [run_dir / STATE_DIR / POOL_STATUS_FILE]
    steps_root = run_dir / STATE_DIR / "steps"
    if steps_root.exists():
        try:
            for step_dir in steps_root.iterdir():
                if step_dir.is_dir():
                    step_pool = step_dir / POOL_STATUS_FILE
                    if step_pool.exists():
                        pool_status_candidates.append(step_pool)
        except OSError:
            pass

    for pool_status_path in pool_status_candidates:
        if not pool_status_path.exists():
            continue
        try:
            from metaproc.runpool.status import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
                is_pool_alive,
                read_status,
            )

            pool_status = read_status(pool_status_path)
            pending_retries += pool_status.pending_retries
            if is_pool_alive(pool_status):
                pool_alive = True
        except Exception:
            log.warning("Failed to read pool status from %s", pool_status_path, exc_info=True)

    # System metrics
    system: SystemMetrics | None = None
    if include_system:
        try:
            pressure = measure_pressure()
            proc_count, rss_bytes = _measure_subprocesses()
            system = SystemMetrics(
                memory_available_pct=pressure.available_pct,
                pressure_level=pressure.level.value,
                swap_used_gb=pressure.swap_used_gb,
                subprocess_count=proc_count,
                rss_bytes=rss_bytes,
            )
        except Exception:
            log.warning("Failed to measure system metrics", exc_info=True)

    # Orchestrator-lease liveness: between fan-out steps the pool is not
    # alive but the DAG orchestrator still is (it's about to launch the next
    # step). Consulting the lease heartbeat closes that window so
    # ``metaproc status`` no longer reports COMPLETE while the process keeps
    # running. The lease file lives at ``<run-dir>/.state/orchestrator-lease.yaml``
    # for single-process runs and at ``<run-dir>/<process>/.state/…`` for DAG
    # runs; scan both.
    orchestrator_alive = is_orchestrator_alive(run_dir)
    if not orchestrator_alive:
        try:
            for child in run_dir.iterdir():
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if is_orchestrator_alive(child):
                    orchestrator_alive = True
                    break
        except OSError:
            pass

    # A run is active if items are running OR retries are pending with a live
    # pool OR an orchestrator lease is still heartbeating.
    is_active = any_running or (pending_retries > 0 and pool_alive) or orchestrator_alive

    process_execution_state, process_error, step_errors = _read_process_execution(run_dir)
    step_entries: list[StepStatusEntry] = []
    process_state: Literal["current", "stale"] | None = None
    if plan is not None:
        variant_counts_by_step = {v.variant: v.counts for v in variant_statuses}
        for step in plan.steps:
            step_entries.append(
                _build_step_status_entry(
                    run_dir,
                    plan,
                    step,
                    variant_counts_by_step,
                    step_errors,
                )
            )
        non_current = sum(
            1 for entry in step_entries if entry.state in (StepState.stale, StepState.invalidated)
        )
        process_state = "stale" if non_current > 0 else "current"

    return RunStatus(
        run_dir=run_dir,
        started_at=started_at,
        elapsed=elapsed,
        is_active=is_active,
        variants=variant_statuses,
        totals=totals,
        pending_retries=pending_retries,
        system=system,
        steps=step_entries,
        process_state=process_state,
        process_execution_state=process_execution_state,
        process_error=process_error,
        items_running=any_running,
        orchestrator_alive=orchestrator_alive,
    )


def _build_step_status_entry(
    run_dir: Path,
    plan: Plan,
    step: ResolvedStep,
    variant_counts: dict[str, ProgressCounts],
    step_errors: dict[str, str],
) -> StepStatusEntry:
    """Construct one ``StepStatusEntry`` for *step* under *run_dir*.

    ``current_hash`` is computed via ``fingerprint_step``; missing-runbook
    errors are surfaced via a None value rather than crashing the whole
    status read.
    """
    state = compute_step_state(run_dir, plan, step.step_id)
    recorded = recorded_step_hash(run_dir, step.step_id)
    try:
        current = fingerprint_step(step)
    except FileNotFoundError as exc:
        log.warning(
            "could not compute current fingerprint for step %r: %s",
            step.step_id,
            exc,
        )
        current = None

    item_counts: dict[str, int] | None = None
    if step.fan_out is not None:
        counts = variant_counts.get(step.step_id)
        if counts is not None:
            item_counts = {
                "completed": counts.completed + counts.cached,
                "total": counts.total,
            }

    reason: str | None = None
    execution_error = step_errors.get(step.step_id)
    if execution_error is not None:
        reason = f"last execution failed: {execution_error}"
    elif state == StepState.stale and recorded is not None and current is not None:
        reason = f"definition changed (was {recorded}, now {current})"
    elif state == StepState.invalidated:
        reason = "will rerun: marked .stale by --force or fingerprint cascade"
    elif state == StepState.in_flight:
        reason = "currently running"

    return StepStatusEntry(
        step_id=step.step_id,
        state=state,
        recorded_hash=recorded,
        current_hash=current,
        item_counts=item_counts,
        reason=reason,
    )


def _read_process_execution(
    run_dir: Path,
) -> tuple[
    Literal["running", "completed", "failed", "cancelled"] | None,
    str | None,
    dict[str, str],
]:
    """Return the execution state, summary error, and per-step errors."""
    path = run_dir / STATE_DIR / "process-status.yaml"
    if not path.exists():
        return None, None, {}
    try:
        raw = read_yaml_file(path)
    except (OSError, YAMLError, ValueError):
        return None, None, {}
    if not isinstance(raw, dict):
        return None, None, {}

    raw_state = raw.get("state")
    execution_state = (
        raw_state if raw_state in {"running", "completed", "failed", "cancelled"} else None
    )
    step_errors: dict[str, str] = {}
    steps = raw.get("steps")
    if isinstance(steps, dict):
        for step_id, entry in steps.items():
            if not isinstance(step_id, str) or not isinstance(entry, dict):
                continue
            if entry.get("state") != "failed":
                continue
            error = entry.get("error")
            step_errors[step_id] = (
                error if isinstance(error, str) and error else "error not recorded"
            )

    process_error = None
    if execution_state == "failed" and step_errors:
        step_id, error = next(iter(step_errors.items()))
        process_error = f"{step_id}: {error}"
    elif execution_state == "failed":
        process_error = "process failed without a recorded step error"
    elif execution_state == "cancelled":
        process_error = "process was cancelled"
    return execution_state, process_error, step_errors


# ── Wait ─────────────────────────────────────────────────────────


def wait_for_completion(
    run_dir: Path,
    *,
    variant: str | None = None,
    timeout: float | None = None,
    interval: float = 10.0,
    include_system: bool = False,
) -> tuple[RunStatus, int]:
    """Poll scan_run_status until no items are running or pending, or timeout.

    Returns (final_status, exit_code):
      exit_code 0 = all completed, 1 = failures exist, 2 = timeout
    """
    start = time.monotonic()
    while True:
        status = scan_run_status(run_dir, variant=variant, include_system=include_system)
        totals = status.totals

        if status.process_execution_state in ("failed", "cancelled"):
            return status, 1

        # Terminal: nothing running or pending, and not active (which accounts
        # for pending retries with a live pool).
        if (
            totals.running == 0
            and totals.pending == 0
            and not status.is_active
            and status.process_execution_state != "running"
        ):
            exit_code = 1 if totals.failed > 0 else 0
            return status, exit_code

        # Timeout check
        if timeout is not None and (time.monotonic() - start) >= timeout:
            return status, 2

        time.sleep(interval)


# ── Check ────────────────────────────────────────────────────────


def check_completion(status: RunStatus, condition: str) -> CheckResult:
    """Check a completion condition against a RunStatus.

    Conditions:
      "completed" — exit 0 if all items completed, 1 if failures, 2 if still running
      "no-failures" — exit 0 if no failures, 1 otherwise
    """
    totals = status.totals

    if condition not in {"completed", "no-failures"}:
        msg = f"Unknown check condition: {condition!r}"
        raise ValueError(msg)

    if status.process_execution_state in ("failed", "cancelled"):
        return CheckResult(
            passed=False,
            exit_code=1,
            reason=status.process_error or f"Process {status.process_execution_state}",
        )

    if condition == "completed":
        if status.process_execution_state == "running":
            return CheckResult(passed=False, exit_code=2, reason="Run still in progress")
        if totals.running > 0 or totals.pending > 0:
            return CheckResult(passed=False, exit_code=2, reason="Run still in progress")
        if totals.failed > 0:
            return CheckResult(
                passed=False,
                exit_code=1,
                reason=f"{totals.failed} items failed",
            )
        return CheckResult(passed=True, exit_code=0, reason="All items completed")

    if condition == "no-failures":
        if totals.failed > 0:
            return CheckResult(
                passed=False,
                exit_code=1,
                reason=f"{totals.failed} items failed",
            )
        return CheckResult(passed=True, exit_code=0, reason="No failures")

    msg = f"Unknown check condition: {condition!r}"
    raise ValueError(msg)


# ── Viz progress snapshot ────────────────────────────────────────

# NodeProgress.state has a wider vocabulary than StepStatus — "blocked" and
# "skipped" are DAG-level judgments. Step-level scanning only emits the
# StepStatus-derived subset below; the viz layer can overlay blocked/skipped
# later if it computes that from the DAG.


NodeProgressState = Literal["pending", "running", "completed", "failed", "blocked", "skipped"]


def scan_step_progress(run_dir: Path, plan: Plan) -> ProgressSnapshot:
    """Scan ``run_dir`` and emit one :class:`NodeProgress` per step in ``plan``.

    Fan-out steps aggregate per-item status files under variant subdirs.
    Scalar steps read the single ``.state/status.yaml`` at ``run_dir/step_id/``.
    Missing directories yield a ``pending`` entry with ``total=0`` — no error.
    """
    nodes: dict[str, NodeProgress] = {}
    for step in plan.steps:
        nodes[step.step_id] = _scan_step(run_dir, step)
    return ProgressSnapshot(
        run_dir=str(run_dir),
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        nodes=nodes,
    )


def _scan_step(run_dir: Path, step: ResolvedStep) -> NodeProgress:
    step_state_root = run_dir / STATE_DIR / "tasks" / step.step_id
    if step.fan_out is not None:
        return _scan_fan_out_step(step_state_root, expected_total=len(step.fan_out.items))
    return _scan_scalar_step(step_state_root)


def _scan_scalar_step(state_dir: Path) -> NodeProgress:
    record = read_status_at(state_dir) if state_dir.exists() else None
    if record is None:
        return NodeProgress(state="pending", completed=0, total=1)
    state = _map_step_status_to_node_state(record.state)
    completed = 1 if record.state == "completed" else 0
    last_error = record.error if record.state == "failed" else None
    return NodeProgress(
        state=state,
        completed=completed,
        total=1,
        last_error=last_error,
    )


def _scan_fan_out_step(step_state_root: Path, expected_total: int) -> NodeProgress:
    """Scan a fan-out step's per-task state dirs.

    *step_state_root* is ``<run>/.state/tasks/<step_id>/``. Each immediate
    subdirectory is one item's state dir containing ``status.yaml`` directly.
    """
    if not step_state_root.exists():
        return NodeProgress(state="pending", completed=0, total=expected_total)
    all_statuses: list[StatusRecord] = []
    for item_state in sorted(step_state_root.iterdir()):
        if not item_state.is_dir():
            continue
        record = read_status_at(item_state)
        if record is not None:
            all_statuses.append(record)
    counts = compute_progress(all_statuses, total=expected_total)
    return NodeProgress(
        state=_derive_fan_out_state(counts),
        completed=counts.completed + counts.cached,
        total=counts.total,
        last_error=_first_error(all_statuses),
    )


def _map_step_status_to_node_state(
    state: str,
) -> NodeProgressState:
    if state in {"completed", "cached"}:
        return "completed"
    if state == "running":
        return "running"
    if state == "failed":
        return "failed"
    return "pending"


def _derive_fan_out_state(counts: ProgressCounts) -> NodeProgressState:
    if counts.running > 0:
        return "running"
    if counts.failed > 0:
        return "failed"
    if counts.total > 0 and counts.completed + counts.cached >= counts.total:
        return "completed"
    return "pending"


def _first_error(statuses: list[StatusRecord]) -> str | None:
    for s in statuses:
        if s.state == "failed" and s.error:
            return s.error
    return None
