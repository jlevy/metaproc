"""Core stats computation — all aggregation logic.

``compute_run_stats()`` is the single entry point.  It calls
``scan_run_status()`` for progress data, runs the one-pass event analysis
for throughput/pool/resources, and returns a ``RunStats`` Pydantic model.
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ruamel.yaml import YAMLError

from metaproc.engine.run_status import RunStatus
from metaproc.logutil.parsing import create_parser, detect_adapter
from metaproc.runpool.event_models import RunPoolEvent
from metaproc.runpool.status import FailureCounts

log = logging.getLogger(__name__)

from metaproc import paths as paths_mod
from metaproc.engine.run_status import TimingStats, scan_run_status
from metaproc.io import artifact_exists, iter_artifact_paths, iter_text_lines
from metaproc.paths import LOGS_DIR, POOL_EVENTS_FILE, POOL_STATUS_FILE, SCALE_STATE_FILE, STATE_DIR
from metaproc.runpool.concurrency import ConcurrencyPlan
from metaproc.runpool.event_reader import read_runpool_events
from metaproc.runpool.status import PressureStatus, ScaleState, read_scale_state, read_status
from metaproc.stats.analysis import analyze_runpool_events
from metaproc.stats.models import (
    APIStats,
    HourlyBucket,
    PoolFinding,
    PoolStats,
    ProgressStats,
    ResourceStats,
    RunPoolAnalysis,
    RunStats,
    ThroughputStats,
    VariantProgress,
)

ALL_SECTIONS = frozenset({"progress", "throughput", "timing", "pool", "api", "resources"})


def compute_run_stats(
    run_dir: Path,
    *,
    sections: frozenset[str] | None = None,
    variant: str | None = None,
    worker: str | None = None,
    include_system: bool = True,
) -> RunStats:
    """Compute unified run statistics.

    Parameters
    ----------
    run_dir:
        Path to the run directory.
    sections:
        Which stat sections to compute. ``None`` means all.
    variant:
        Filter to a specific variant.
    include_system:
        Whether to measure live system metrics (memory, subprocesses).
    """
    want = sections or ALL_SECTIONS

    progress: ProgressStats | None = None
    throughput: ThroughputStats | None = None
    timing: TimingStats | None = None
    pool: PoolStats | None = None
    resources: ResourceStats | None = None

    # Always scan run status (needed for progress and as a baseline)
    run_status = scan_run_status(run_dir, variant=variant, include_system=include_system)

    # Progress section
    if "progress" in want:
        variants_map: dict[str, VariantProgress] = {}
        for vs in run_status.variants:
            c = vs.counts
            done = c.completed + c.cached
            pct = (done * 100.0 / c.total) if c.total > 0 else 0.0
            variants_map[vs.variant] = VariantProgress(
                completed=done,
                running=c.running,
                failed=c.failed,
                pending=c.pending,
                total=c.total,
                pct=pct,
                timing=vs.timing,
            )

        t = run_status.totals
        wall_time = run_status.elapsed.total_seconds() if run_status.elapsed else 0.0

        # ETA: pending / (completed / wall_time) — overall-rate extrapolation
        eta_s: float | None = None
        done = t.completed + t.cached
        if t.pending > 0 and done > 0 and wall_time > 0:
            rate_per_s = done / wall_time
            if rate_per_s > 0:
                eta_s = t.pending / rate_per_s

        progress = ProgressStats(
            total_items=t.total,
            completed=done,
            failed=t.failed,
            running=t.running,
            pending=t.pending,
            variants=variants_map,
            wall_time_s=wall_time,
            eta_s=eta_s,
        )

    # Event-based sections — parse runpool events if needed
    need_events = want & {"throughput", "timing", "pool", "resources"}
    analysis: RunPoolAnalysis | None = None

    if need_events:
        all_events = _collect_runpool_events(run_dir, worker=worker)
        if all_events:
            analysis = analyze_runpool_events(all_events)

    # Throughput section
    if "throughput" in want and analysis:
        unique_completed = run_status.totals.completed + run_status.totals.cached
        throughput = _compute_throughput(
            analysis, run_status.elapsed, unique_completed=unique_completed
        )

    # Timing section (from event durations — more accurate than status.yaml)
    if "timing" in want and analysis and analysis.successful_exit_durations_s:
        successful_durations = analysis.successful_exit_durations_s
        if successful_durations:
            avg = sum(successful_durations) / len(successful_durations)
            p50 = None
            p95 = None
            p99 = None
            if len(successful_durations) >= 2:
                sorted_d = sorted(successful_durations)
                q = statistics.quantiles(sorted_d, n=100)
                p50 = q[49]
                p95 = q[94]
                p99 = q[98]
            timing = TimingStats(
                avg_seconds=avg,
                min_seconds=min(successful_durations),
                max_seconds=max(successful_durations),
                p50_seconds=p50,
                p95_seconds=p95,
                p99_seconds=p99,
            )

    # Pool section
    if "pool" in want and analysis:
        pool = _compute_pool_stats(analysis, run_dir)

    # Resources section
    if "resources" in want and analysis:
        resources = _compute_resource_stats(analysis, run_status)

    # API stats (from agent logs)
    api = None
    if "api" in want:
        api = _compute_api_stats(run_dir)

    return RunStats(
        run_dir=str(run_dir),
        computed_at=datetime.now(UTC),
        progress=progress,
        throughput=throughput,
        timing=timing,
        pool=pool,
        api=api,
        resources=resources,
    )


def _collect_runpool_events(run_dir: Path, worker: str | None = None):  # noqa: ANN202
    """Collect runpool events from all workers (or a specific worker).

    Event files live under ``<run_dir>/.logs/runpool/`` for new runs.
    Exact legacy root and worker paths are also supported for old runs.
    """

    all_events: list[RunPoolEvent] = []

    event_paths: list[Path] = []
    if worker is None:
        event_paths.append(paths_mod.runpool_events_for_read(run_dir))
        steps_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
        if steps_root.is_dir():
            event_paths.extend(
                iter_artifact_paths(steps_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}")
            )
        legacy_steps_root = paths_mod.run_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
        if not paths_mod.is_v2_run_layout(run_dir) and legacy_steps_root.is_dir():
            event_paths.extend(
                iter_artifact_paths(legacy_steps_root, f"*/{paths_mod.POOL_EVENTS_FILE}")
            )

    if worker is not None:
        event_paths.append(paths_mod.runpool_worker_events_for_read(run_dir, worker))
    else:
        workers_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.WORKERS_SUBDIR
        if workers_root.is_dir():
            event_paths.extend(
                iter_artifact_paths(workers_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}")
            )
        legacy_worker_paths = iter_artifact_paths(
            paths_mod.run_logs_dir(run_dir), f"worker-*/{paths_mod.POOL_EVENTS_FILE}"
        )
        if not paths_mod.is_v2_run_layout(run_dir):
            event_paths.extend(legacy_worker_paths)

    seen: set[Path] = set()
    for events_file in event_paths:
        if events_file in seen or not events_file.exists():
            continue
        seen.add(events_file)
        all_events.extend(read_runpool_events(events_file))

    # Sort by timestamp for correct time-series ordering
    all_events.sort(key=lambda e: e.ts)
    return all_events


def _compute_throughput(
    analysis: RunPoolAnalysis,
    elapsed: object,
    *,
    unique_completed: int,
) -> ThroughputStats | None:
    """Compute throughput metrics from analysis results.

    ``unique_completed`` is the count of distinct items completed (from
    ``scan_run_status`` — one per record.md written). ``total_process_runs``
    is the count of process starts, which is higher when items are retried.
    The retry factor is total_runs / unique_items, NOT total_runs / successful
    exits (every retry that succeeds is still a successful exit).
    """

    total_runs = analysis.total_process_runs
    if unique_completed == 0 and analysis.successful_exits == 0:
        return None
    # Fallback if run_status hasn't caught up yet
    if unique_completed == 0:
        unique_completed = analysis.successful_exits

    # Wall time from event timestamps
    wall_hours = 0.0
    if isinstance(elapsed, timedelta):
        wall_hours = elapsed.total_seconds() / 3600.0
    if wall_hours <= 0:
        wall_hours = 1.0 / 3600.0  # minimum 1 second

    items_per_hour = unique_completed / wall_hours
    retry_factor = total_runs / unique_completed if unique_completed > 0 else 0.0
    retry_pct = ((total_runs - unique_completed) / total_runs * 100.0) if total_runs > 0 else 0.0

    # Hourly breakdown from exit timestamps
    hourly = _compute_hourly_breakdown(analysis)

    # Recent rate: completions in the last 30 minutes, extrapolated to per-hour
    recent_rate = _compute_recent_rate(analysis, window_minutes=30) or items_per_hour

    return ThroughputStats(
        items_per_hour=round(items_per_hour, 1),
        recent_items_per_hour=round(recent_rate, 1),
        unique_completed=unique_completed,
        total_process_runs=total_runs,
        retry_factor=round(retry_factor, 2),
        retry_pct=round(retry_pct, 1),
        hourly_breakdown=hourly,
    )


def _compute_recent_rate(analysis: RunPoolAnalysis, *, window_minutes: int) -> float | None:
    """Rate of successful completions in the last ``window_minutes``, per hour.

    Uses the last exit timestamp as "now" so paused/finished runs still reflect
    the tail-end rate rather than 0.
    """

    ts_strs = analysis.successful_exit_timestamps
    if not ts_strs:
        return None
    try:
        parsed = [datetime.fromisoformat(t) for t in ts_strs]
    except (ValueError, TypeError):
        return None
    end = max(parsed)
    window_start = end - timedelta(minutes=window_minutes)
    count = sum(1 for t in parsed if t >= window_start)
    if count == 0:
        return 0.0
    return count * (60.0 / window_minutes)


def _compute_hourly_breakdown(analysis: RunPoolAnalysis) -> list[HourlyBucket]:
    """Group successful completions into hourly buckets from exit timestamps."""
    if not analysis.successful_exit_timestamps:
        return []

    buckets: dict[str, int] = {}
    for ts_str in analysis.successful_exit_timestamps:
        try:
            t = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        # Bucket by hour in local ISO format (e.g., "2026-04-10 20:00")
        key = t.strftime("%Y-%m-%d %H:00")
        buckets[key] = buckets.get(key, 0) + 1

    return [HourlyBucket(hour=h, completed=c, rate=float(c)) for h, c in sorted(buckets.items())]


def _compute_pool_stats(analysis: RunPoolAnalysis, run_dir: Path) -> PoolStats:
    """Compute pool health stats from analysis and live status.

    Pool status files live under ``<run_dir>/.state/workers/`` for cloud workers
    and ``<run_dir>/.state/steps/`` for step pools. Exact legacy worker paths
    remain readable for old runs.
    """

    current_concurrency = 0
    max_concurrency = 0
    pressure: PressureStatus | None = None
    worker_ids: list[str] = []
    live_worker_caps: dict[str, int] = {}
    provider_ceiling = 0
    memory_ceiling = 0
    operator_cap = 0
    effective_target = 0
    recent_rate_limits = 0
    bottleneck_counts: dict[str, int] = {}
    worker_concurrency_plans: dict[str, ConcurrencyPlan] = {}
    active_count = 0
    pending_count = 0
    completed_count = 0
    failed_count = 0
    killed_count = 0
    # Client-internal retry totals aggregated across all worker status files.
    # These are 429s/timeouts/bad-output absorbed by RetryPolicy — the
    # process still exits 0, so they never show up as a process-level failure.
    live_failure_counts: FailureCounts | None = None

    raw_max = analysis.metadata.get("max_concurrency", 0)
    raw_max_concurrency = int(raw_max) if isinstance(raw_max, (int, float, str)) else 0

    workers_logs_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.WORKERS_SUBDIR
    if workers_logs_root.is_dir():
        for d in sorted(workers_logs_root.iterdir()):
            if d.is_dir() and artifact_exists(d / paths_mod.RUNPOOL_EVENTS_FILE):
                worker_ids.append(d.name)

    legacy_logs_root = run_dir / LOGS_DIR
    if not paths_mod.is_v2_run_layout(run_dir) and legacy_logs_root.is_dir():
        for d in sorted(legacy_logs_root.iterdir()):
            if (
                d.is_dir()
                and d.name.startswith("worker-")
                and artifact_exists(d / POOL_EVENTS_FILE)
            ) and d.name not in worker_ids:
                worker_ids.append(d.name)

    # Read live status from worker/step status files, aggregating across scopes.
    state_root = paths_mod.run_state_dir(run_dir)
    status_files: list[tuple[str, Path]] = []
    if state_root.is_dir():
        for w in worker_ids:
            sf = paths_mod.worker_status_file_for_read(run_dir, w)
            if sf.exists():
                status_files.append((w, sf))
        steps_root = state_root / paths_mod.STEPS_SUBDIR
        if steps_root.is_dir():
            for step_dir in sorted(steps_root.iterdir()):
                sf = step_dir / POOL_STATUS_FILE
                if sf.exists():
                    status_files.append((step_dir.name, sf))
        legacy = state_root / POOL_STATUS_FILE
        if not status_files and legacy.exists():
            status_files.append(("local", legacy))
    older_legacy = run_dir / LOGS_DIR / POOL_STATUS_FILE
    if not status_files and older_legacy.exists():
        status_files.append(("local", older_legacy))

    aggregated_counts = FailureCounts()
    pressure_rank = {"normal": 0, "elevated": 1, "high": 2, "critical": 3}
    for worker_id, status_file in status_files:
        try:
            live = read_status(status_file)
        except (OSError, ValueError) as exc:
            log.warning("Skipping unreadable pool status %s: %s", status_file, exc)
            continue
        current_concurrency += live.current_concurrency
        max_concurrency += live.max_concurrency
        active_count += live.active_count
        pending_count += live.pending_count
        completed_count += live.completed_count
        failed_count += live.failed_count
        killed_count += live.killed_count
        live_worker_caps[worker_id] = live.max_concurrency
        if (
            pressure is None
            or pressure_rank[live.pressure.level.value] > pressure_rank[pressure.level.value]
        ):
            pressure = live.pressure
        if live.controller is not None:
            provider_ceiling += live.controller.provider_ceiling
            memory_ceiling += live.controller.memory_ceiling
            operator_cap += live.controller.operator_cap
            effective_target += live.controller.effective_target
            recent_rate_limits += live.controller.recent_rate_limits
            bottleneck_counts[live.controller.bottleneck] = (
                bottleneck_counts.get(live.controller.bottleneck, 0) + 1
            )
        if live.concurrency_plan is not None:
            worker_concurrency_plans[worker_id] = live.concurrency_plan
        # Sum live failure counts across workers
        fc = live.failure_counts
        aggregated_counts = FailureCounts(
            rate_limited=aggregated_counts.rate_limited + fc.rate_limited,
            server_error=aggregated_counts.server_error + fc.server_error,
            timeout=aggregated_counts.timeout + fc.timeout,
            invalid_output=aggregated_counts.invalid_output + fc.invalid_output,
            crash=aggregated_counts.crash + fc.crash,
            unknown=aggregated_counts.unknown + fc.unknown,
        )
        live_failure_counts = aggregated_counts

    desired_state = _read_desired_scale_state(run_dir)
    if max_concurrency == 0:
        max_concurrency = raw_max_concurrency

    bottleneck = None
    if bottleneck_counts:
        bottleneck = max(
            bottleneck_counts.items(),
            key=lambda item: (item[1], item[0] == "DSQ-bound", item[0] == "memory-bound"),
        )[0]

    total_kills = sum(analysis.kill_reasons.values())
    findings = _pool_findings(
        num_workers=max(1, len(worker_ids)),
        desired_workers=desired_state.desired_workers if desired_state is not None else None,
        active_count=active_count,
        pending_count=pending_count,
        max_concurrency=max_concurrency,
        desired_max_concurrency=(
            desired_state.desired_max_concurrency if desired_state is not None else None
        ),
        effective_target=effective_target or None,
        bottleneck=bottleneck,
        concurrency_plans=worker_concurrency_plans,
    )
    return PoolStats(
        num_workers=max(1, len(worker_ids)),
        worker_ids=worker_ids,
        desired_workers=desired_state.desired_workers if desired_state is not None else None,
        active_count=active_count,
        pending_count=pending_count,
        completed_count=completed_count,
        failed_count=failed_count,
        killed_count=killed_count,
        current_concurrency=current_concurrency,
        max_concurrency=max_concurrency,
        desired_max_concurrency=(
            desired_state.desired_max_concurrency if desired_state is not None else None
        ),
        scale_generation=desired_state.generation if desired_state is not None else None,
        live_worker_caps=live_worker_caps,
        bottleneck=bottleneck,
        provider_ceiling=provider_ceiling or None,
        memory_ceiling=memory_ceiling or None,
        operator_cap=operator_cap or None,
        effective_target=effective_target or None,
        recent_rate_limits=recent_rate_limits,
        pressure=pressure,
        concurrency_plan=(
            next(iter(worker_concurrency_plans.values()))
            if len(worker_concurrency_plans) == 1
            else None
        ),
        worker_concurrency_plans=worker_concurrency_plans,
        concurrency_adjustments=analysis.concurrency_adjustments,
        findings=findings,
        process_starts=analysis.total_process_runs,
        process_successful_exits=analysis.successful_exits,
        process_failed_exits=analysis.failed_exits + total_kills,
        process_failure_counts=analysis.failure_counts,
        process_kills_by_reason=analysis.kill_reasons,
        runner_failure_counts=live_failure_counts or FailureCounts(),
    )


def _pool_findings(
    *,
    num_workers: int,
    desired_workers: int | None,
    active_count: int,
    pending_count: int,
    max_concurrency: int,
    desired_max_concurrency: int | None,
    effective_target: int | None,
    bottleneck: str | None,
    concurrency_plans: dict[str, ConcurrencyPlan],
) -> list[PoolFinding]:
    findings: list[PoolFinding] = []

    if desired_workers is not None and num_workers < desired_workers:
        findings.append(
            PoolFinding(
                severity="warning",
                code="desired_workers_unmet",
                message=(
                    f"RunPool has {num_workers} workers but scale-state requests {desired_workers}."
                ),
                details={"active_workers": num_workers, "desired_workers": desired_workers},
            )
        )

    if (
        desired_max_concurrency is not None
        and max_concurrency > 0
        and max_concurrency < desired_max_concurrency
    ):
        findings.append(
            PoolFinding(
                severity="warning",
                code="desired_concurrency_unmet",
                message=(
                    f"Live max concurrency is {max_concurrency}, below desired "
                    f"{desired_max_concurrency}."
                ),
                details={
                    "max_concurrency": max_concurrency,
                    "desired_max_concurrency": desired_max_concurrency,
                },
            )
        )

    target = effective_target or max_concurrency
    if pending_count > 0 and target > 0 and active_count < target:
        severity = "warning" if bottleneck in {None, "uncapped", "none"} else "info"
        findings.append(
            PoolFinding(
                severity=severity,
                code="pending_queue_below_target",
                message=(
                    f"{pending_count} items are pending while only {active_count}/{target} "
                    f"target slots are active."
                ),
                details={
                    "active_count": active_count,
                    "pending_count": pending_count,
                    "target": target,
                    "bottleneck": bottleneck,
                },
            )
        )

    for worker_id, plan in sorted(concurrency_plans.items()):
        if plan.configured_max_concurrency < plan.requested_max_concurrency:
            findings.append(
                PoolFinding(
                    severity="warning",
                    code="configured_concurrency_below_requested",
                    message=(
                        f"{worker_id} configured max concurrency "
                        f"{plan.configured_max_concurrency} is below requested "
                        f"{plan.requested_max_concurrency}."
                    ),
                    details=_concurrency_plan_details(worker_id, plan),
                )
            )
        if plan.host_max_concurrency < plan.requested_max_concurrency:
            findings.append(
                PoolFinding(
                    severity="warning",
                    code="host_concurrency_below_requested",
                    message=(
                        f"{worker_id} host max concurrency {plan.host_max_concurrency} "
                        f"is below requested {plan.requested_max_concurrency}."
                    ),
                    details=_concurrency_plan_details(worker_id, plan),
                )
            )
        if plan.initial_concurrency < plan.configured_max_concurrency:
            severity = (
                "warning"
                if plan.initial_concurrency <= max(1, plan.configured_max_concurrency // 2)
                else "info"
            )
            findings.append(
                PoolFinding(
                    severity=severity,
                    code="initial_concurrency_below_configured",
                    message=(
                        f"{worker_id} started at concurrency {plan.initial_concurrency}, "
                        f"below configured {plan.configured_max_concurrency}; "
                        f"limiting_factor={plan.limiting_factor}."
                    ),
                    details=_concurrency_plan_details(worker_id, plan),
                )
            )

    return findings


def _concurrency_plan_details(
    worker_id: str, plan: ConcurrencyPlan
) -> dict[str, int | str | float | None]:
    return {
        "worker_id": worker_id,
        "execution_profile": plan.execution_profile,
        "backend": plan.backend,
        "requested_max_concurrency": plan.requested_max_concurrency,
        "configured_max_concurrency": plan.configured_max_concurrency,
        "host_max_concurrency": plan.host_max_concurrency,
        "initial_concurrency": plan.initial_concurrency,
        "initial_concurrency_estimate": plan.initial_concurrency_estimate,
        "initial_memory_budget_fraction": plan.initial_memory_budget_fraction,
        "estimated_process_rss_bytes": plan.estimated_process_rss_bytes,
        "limiting_factor": plan.limiting_factor,
    }


def _read_desired_scale_state(run_dir: Path) -> ScaleState | None:
    """Read the latest step-scoped desired topology state, if any."""
    candidates: list[ScaleState] = []
    steps_root = paths_mod.run_state_dir(run_dir) / paths_mod.STEPS_SUBDIR
    step_scale_paths = (
        sorted(steps_root.glob(f"*/{SCALE_STATE_FILE}")) if steps_root.is_dir() else []
    )
    legacy_scale_paths = sorted(run_dir.glob(f"*/{STATE_DIR}/{SCALE_STATE_FILE}"))
    for scale_state_path in [*step_scale_paths, *legacy_scale_paths]:
        try:
            state = read_scale_state(scale_state_path)
        except (FileNotFoundError, ValueError, YAMLError):
            continue
        if state.desired_workers is None and state.desired_max_concurrency is None:
            continue
        candidates.append(state)
    if not candidates:
        return None
    return max(candidates, key=lambda state: state.updated_at)


def _compute_resource_stats(
    analysis: RunPoolAnalysis,
    run_status: object,
) -> ResourceStats | None:
    """Compute resource usage from analysis and system metrics."""

    rss_values = analysis.peak_rss_values
    if not rss_values:
        return None

    avg_rss = sum(rss_values) // len(rss_values)
    max_rss = max(rss_values)

    swap_gb = 0.0
    if isinstance(run_status, RunStatus) and run_status.system:
        swap_gb = run_status.system.swap_used_gb

    return ResourceStats(
        avg_peak_rss_bytes=avg_rss,
        max_peak_rss_bytes=max_rss,
        swap_used_gb=swap_gb,
        kills_by_reason=analysis.kill_reasons,
    )


def _compute_api_stats(run_dir: Path) -> APIStats | None:
    """Compute API/LLM usage stats from agent log files.

    Scans ``*.jsonl`` agent logs in variant/item directories for model,
    cost, and tool_call information.
    """

    total_calls = 0
    models: dict[str, int] = {}
    total_cost = 0.0
    error_count = 0
    items_with_logs = 0

    # Walk variant/item directories looking for agent logs under <item>/.logs/
    for variant_dir in sorted(run_dir.iterdir()) if run_dir.is_dir() else []:
        if not variant_dir.is_dir() or variant_dir.name.startswith("."):
            continue
        for item_dir in sorted(variant_dir.iterdir()):
            if not item_dir.is_dir() or item_dir.name.startswith("."):
                continue
            item_logs_dir = item_dir / LOGS_DIR
            log_files: list[Path] = []
            if item_logs_dir.is_dir():
                log_files.extend(iter_artifact_paths(item_logs_dir, "*.jsonl"))
            # Legacy: logs directly in item dir
            log_files.extend(iter_artifact_paths(item_dir, "*.jsonl"))
            if not log_files:
                continue

            items_with_logs += 1
            for log_path in log_files:
                try:
                    first_lines: list[str] = []
                    for _line_no, raw in iter_text_lines(log_path):
                        s = raw.strip()
                        if s:
                            first_lines.append(s)
                        if len(first_lines) >= 20:
                            break
                    adapter = detect_adapter(first_lines)
                    parser = create_parser(adapter)

                    for _line_no, raw in iter_text_lines(log_path):
                        s = raw.strip()
                        if not s or len(s) > 256 * 1024:
                            continue
                        for ev in parser.parse_line(s):
                            if ev.kind == "tool_call":
                                total_calls += 1
                            if ev.kind == "init":
                                raw_dict = ev.raw if isinstance(ev.raw, dict) else {}
                                model = raw_dict.get("model")
                                if model and isinstance(model, str):
                                    models[model] = models.get(model, 0) + 1
                            if ev.kind == "result":
                                if ev.cost_usd is not None:
                                    total_cost += ev.cost_usd
                                if ev.is_error:
                                    error_count += 1
                    for ev in parser.flush():
                        if ev.kind == "result" and ev.cost_usd is not None:
                            total_cost += ev.cost_usd
                except (OSError, ValueError) as exc:
                    log.warning("Skipping malformed API log %s: %s", log_path, exc)

    if items_with_logs == 0:
        return None

    return APIStats(
        total_calls=total_calls,
        models=models,
        total_cost_usd=round(total_cost, 4),
        avg_calls_per_item=round(total_calls / items_with_logs, 1) if items_with_logs > 0 else 0.0,
        error_count=error_count,
    )
