"""CLI text formatters for stats output.

``format_full()`` renders the multi-section report for ``metaproc stats``.
Uses ``prettyfmt`` for duration formatting, aligned columns, and consistent
section headers.
"""

from __future__ import annotations

from prettyfmt import fmt_timedelta

from metaproc.stats.models import RunStats


def format_full(stats: RunStats) -> str:
    """Format RunStats as a full multi-section text report."""
    sections: list[str] = []

    if stats.progress:
        sections.append(_format_progress(stats))

    if stats.throughput:
        sections.append(_format_throughput(stats))

    if stats.timing:
        sections.append(_format_timing(stats))

    if stats.pool:
        sections.append(_format_pool(stats))

    if stats.api:
        sections.append(_format_api(stats))

    if stats.resources:
        sections.append(_format_resources(stats))

    return "\n\n".join(sections)


def _format_progress(stats: RunStats) -> str:
    p = stats.progress
    if not p:
        return ""

    lines: list[str] = []
    lines.append(f"Run: {stats.run_dir}")
    if p.wall_time_s > 0:
        lines.append(f"Wall time: {fmt_timedelta(p.wall_time_s)}")
    status_str = "RUNNING" if p.running > 0 else "COMPLETE"
    lines.append(f"Status: {status_str}")
    if p.eta_s is not None and p.eta_s > 0:
        lines.append(f"ETA: ~{fmt_timedelta(p.eta_s)}")
    lines.append("")

    # Variant table
    header = (
        f"  {'Variant':<35} {'Done':>5} {'Run':>4} {'Fail':>5} {'Pend':>5} {'Total':>6}   {'%':>4}"
    )
    sep = "  " + "\u2500" * 72
    lines.append(header)
    lines.append(sep)

    for vname, vp in p.variants.items():
        pct = f"{vp.pct:.0f}%"
        lines.append(
            f"  {vname:<35} {vp.completed:>5} {vp.running:>4} {vp.failed:>5}"
            f" {vp.pending:>5} {vp.total:>6}   {pct:>4}"
        )

    if len(p.variants) > 1:
        pct = f"{p.completed * 100.0 / p.total_items:.0f}%" if p.total_items > 0 else "0%"
        lines.append(sep)
        lines.append(
            f"  {'Total':<35} {p.completed:>5} {p.running:>4} {p.failed:>5}"
            f" {p.pending:>5} {p.total_items:>6}   {pct:>4}"
        )

    # Timing per variant
    for vname, vp in p.variants.items():
        if vp.timing:
            t = vp.timing
            parts = [
                f"Avg: {fmt_timedelta(t.avg_seconds)}",
                f"Min: {fmt_timedelta(t.min_seconds)}",
                f"Max: {fmt_timedelta(t.max_seconds)}",
            ]
            if t.p50_seconds is not None:
                parts.append(f"p50: {fmt_timedelta(t.p50_seconds)}")
            if t.p95_seconds is not None:
                parts.append(f"p95: {fmt_timedelta(t.p95_seconds)}")
            if t.eta_seconds is not None:
                parts.append(f"ETA: ~{fmt_timedelta(t.eta_seconds)}")
            lines.append(f"\nTiming ({vname}):")
            lines.append(f"  {' | '.join(parts)}")

    return "\n".join(lines)


def _format_throughput(stats: RunStats) -> str:
    t = stats.throughput
    if not t:
        return ""

    lines = ["Throughput:"]
    lines.append(
        f"  Items/hour:     {t.items_per_hour:.1f} (last 30m: {t.recent_items_per_hour:.1f})"
    )
    lines.append(
        f"  Unique items:   {t.unique_completed} completed from {t.total_process_runs} process runs"
    )
    lines.append(
        f"  Retry factor:   {t.retry_factor:.1f}x ({t.retry_pct:.0f}% of runs are retries)"
    )

    if t.hourly_breakdown:
        lines.append("  Hourly:")
        for bucket in t.hourly_breakdown:
            lines.append(f"    {bucket.hour}  {bucket.completed} completed  ({bucket.rate:.0f}/hr)")

    return "\n".join(lines)


def _format_timing(stats: RunStats) -> str:
    t = stats.timing
    if not t:
        return ""

    lines = ["Timing (completed items):"]
    if t.p50_seconds is not None:
        lines.append(f"  p50:    {t.p50_seconds:.0f}s ({fmt_timedelta(t.p50_seconds)})")
    if t.p95_seconds is not None:
        lines.append(f"  p95:    {t.p95_seconds:.0f}s ({fmt_timedelta(t.p95_seconds)})")
    if t.p99_seconds is not None:
        lines.append(f"  p99:    {t.p99_seconds:.0f}s ({fmt_timedelta(t.p99_seconds)})")
    lines.append(f"  avg:    {t.avg_seconds:.0f}s ({fmt_timedelta(t.avg_seconds)})")
    lines.append(f"  min:    {t.min_seconds:.0f}s")
    lines.append(f"  max:    {t.max_seconds:.0f}s ({fmt_timedelta(t.max_seconds)})")

    return "\n".join(lines)


def _format_pool(stats: RunStats) -> str:
    p = stats.pool
    if not p:
        return ""

    lines = ["Pool:"]
    worker_str = ", ".join(p.worker_ids) if p.worker_ids else "local"
    lines.append(f"  Workers:        {p.num_workers} ({worker_str})")
    if p.desired_workers is not None:
        lines.append(f"  Topology:       active={p.num_workers} desired={p.desired_workers}")
    lines.append(
        f"  Concurrency:    {p.current_concurrency}/{p.max_concurrency} max (current/limit)"
    )
    if p.active_count or p.pending_count or p.completed_count or p.failed_count or p.killed_count:
        lines.append(
            "  Queue:          "
            f"active={p.active_count} pending={p.pending_count} "
            f"completed={p.completed_count} failed={p.failed_count} "
            f"killed={p.killed_count}"
        )
    if p.desired_max_concurrency is not None or p.live_worker_caps:
        live_caps = ", ".join(
            f"{worker}={cap}" for worker, cap in sorted(p.live_worker_caps.items())
        )
        if p.desired_max_concurrency is not None and live_caps:
            lines.append(f"  Worker caps:    desired={p.desired_max_concurrency} live={live_caps}")
        elif p.desired_max_concurrency is not None:
            lines.append(f"  Worker caps:    desired={p.desired_max_concurrency}")
        elif live_caps:
            lines.append(f"  Worker caps:    live={live_caps}")
    if p.scale_generation is not None:
        lines.append(f"  Scale gen:      {p.scale_generation}")
    plan = p.concurrency_plan
    if plan is None and len(p.worker_concurrency_plans) == 1:
        plan = next(iter(p.worker_concurrency_plans.values()))
    if plan is not None:
        profile = plan.execution_profile or "-"
        rss_mb = plan.estimated_process_rss_bytes // (1024 * 1024)
        lines.append(
            "  Plan:           "
            f"profile={profile} backend={plan.backend} requested={plan.requested_max_concurrency} "
            f"configured={plan.configured_max_concurrency} initial={plan.initial_concurrency} "
            f"limit={plan.limiting_factor} host={plan.host_max_concurrency} "
            f"rss={rss_mb}MB budget={plan.initial_memory_budget_fraction:.2f}"
        )
    if p.effective_target is not None:
        lines.append(f"  Target:         {p.effective_target}")
    if p.memory_ceiling is not None or p.provider_ceiling is not None:
        lines.append(
            "  Ceilings:       "
            f"memory={p.memory_ceiling if p.memory_ceiling is not None else '-'} "
            f"provider={p.provider_ceiling if p.provider_ceiling is not None else '-'} "
            f"operator={p.operator_cap if p.operator_cap is not None else '-'}"
        )
    if p.bottleneck:
        lines.append(f"  Bottleneck:     {p.bottleneck}")
    if p.recent_rate_limits:
        lines.append(f"  Recent 429s:    {p.recent_rate_limits}")
    if p.findings:
        lines.append("  Findings:")
        for finding in p.findings:
            lines.append(f"    [{finding.severity}] {finding.code}: {finding.message}")
    if p.pressure:
        disk_free = (
            f"{p.pressure.disk_free_gb:.1f}GB" if p.pressure.disk_free_gb is not None else "unknown"
        )
        lines.append(
            f"  Pressure:       {p.pressure.level} ({p.pressure.available_pct:.1f}% available, "
            f"swap={p.pressure.swap_used_gb:.1f}GB, "
            f"swap_delta={p.pressure.swap_delta_gb_per_min:.1f}GB/min, "
            f"disk_free={disk_free})"
        )
    lines.append(f"  Adjustments:    {p.concurrency_adjustments}")

    # Process-level exits: hard terminations visible in runpool events.
    lines.append("\nProcess exits (hard terminations from runpool events):")
    lines.append(f"  Starts:           {p.process_starts}")
    lines.append(f"  Successful (0):   {p.process_successful_exits}")
    lines.append(f"  Failed (non-0):   {p.process_failed_exits}")
    total_kills = sum(p.process_kills_by_reason.values())
    if total_kills > 0:
        kill_parts = ", ".join(f"{k}: {v}" for k, v in p.process_kills_by_reason.items())
        lines.append(f"  Killed:           {total_kills} ({kill_parts})")
    else:
        lines.append("  Killed:           0")

    pfc = p.process_failure_counts
    proc_fail_total = (
        pfc.rate_limited
        + pfc.server_error
        + pfc.invalid_output
        + pfc.timeout
        + pfc.crash
        + pfc.unknown
    )
    lines.append(f"  Failed by class (total {proc_fail_total}):")
    lines.append(f"    rate_limited:   {pfc.rate_limited}")
    lines.append(f"    server_error:   {pfc.server_error}")
    lines.append(f"    invalid_output: {pfc.invalid_output}")
    lines.append(f"    timeout:        {pfc.timeout}")
    lines.append(f"    crash:          {pfc.crash}")
    if pfc.unknown > 0:
        lines.append(f"    unknown:        {pfc.unknown}")

    # Runner-level failure attempts — every batch_failed entry classified
    # across all retry attempts. Covers kills, non-zero exits, and exit=0
    # with invalid outputs. Counted per-attempt, not per-item.
    rfc = p.runner_failure_counts
    retry_total = (
        rfc.rate_limited
        + rfc.server_error
        + rfc.invalid_output
        + rfc.timeout
        + rfc.crash
        + rfc.unknown
    )
    lines.append(
        f"\nRetry attempts by class — all failed attempts across retries (total {retry_total}):"
    )
    lines.append(f"  rate_limited:     {rfc.rate_limited}")
    lines.append(f"  server_error:     {rfc.server_error}")
    lines.append(f"  invalid_output:   {rfc.invalid_output}")
    lines.append(f"  timeout:          {rfc.timeout}")
    lines.append(f"  crash:            {rfc.crash}")
    if rfc.unknown > 0:
        lines.append(f"  unknown:          {rfc.unknown}")

    # Reconciliation: unique completed ≈ starts − (runner failures that
    # never succeeded after retries). Retried-but-eventually-succeeded
    # items account for any remainder.
    if p.process_starts > 0:
        unresolved = max(0, p.process_starts - p.process_successful_exits - total_kills)
        lines.append("")
        lines.append(
            f"  Note: retry_attempts ({retry_total}) include all classified"
            f" failures across retries;"
        )
        lines.append(f"  unresolved process runs (exit!=0 and not killed): {unresolved}.")

    return "\n".join(lines)


def _format_api(stats: RunStats) -> str:
    a = stats.api
    if not a:
        return ""

    lines = ["API Usage:"]
    lines.append(f"  Total calls:    {a.total_calls:,}")
    model_str = ", ".join(f"{m} ({c:,})" for m, c in a.models.items())
    lines.append(f"  Models:         {model_str}")
    lines.append(f"  Cost:           ${a.total_cost_usd:.2f}")
    lines.append(f"  Avg calls/item: {a.avg_calls_per_item:.1f}")
    lines.append(f"  Errors:         {a.error_count:,}")

    return "\n".join(lines)


def _format_resources(stats: RunStats) -> str:
    r = stats.resources
    if not r:
        return ""

    avg_mb = r.avg_peak_rss_bytes / (1024 * 1024)
    max_mb = r.max_peak_rss_bytes / (1024 * 1024)

    lines = ["Resources:"]
    lines.append(f"  Peak RSS:       {avg_mb:.0f} MB (avg per process)")
    lines.append(f"  Max RSS:        {max_mb:.0f} MB")
    lines.append(f"  Swap:           {r.swap_used_gb:.1f} GB")

    if r.kills_by_reason:
        kill_parts = ", ".join(f"{k}: {v}" for k, v in r.kills_by_reason.items())
        lines.append(f"  Kills:          {sum(r.kills_by_reason.values())} ({kill_parts})")
    else:
        lines.append("  Kills:          0")

    return "\n".join(lines)
