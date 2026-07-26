"""One-pass runpool event analysis — shared by charts and stats.

Processes the typed event list once and returns a ``RunPoolAnalysis`` model
containing both chart-oriented data (time-series, taxonomy, annotations) and
stats-oriented data (throughput, failures, timing distributions).
"""

from __future__ import annotations

from datetime import datetime

from metaproc.runpool.event_models import (
    ConcurrencyAdjustEvent,
    PoolShutdownEvent,
    PoolStartEvent,
    PressureCheckEvent,
    ProcessExitEvent,
    ProcessKillEvent,
    ProcessStartEvent,
    RunPoolEvent,
)
from metaproc.runpool.status import FailureCounts
from metaproc.stats.models import RunPoolAnalysis, TimePoint


def analyze_runpool_events(events: list[RunPoolEvent]) -> RunPoolAnalysis:
    """Process all runpool events in a single pass.

    Returns a ``RunPoolAnalysis`` with both chart and stats data.
    """
    taxonomy: dict[str, int] = {}
    metadata: dict[str, object] = {}
    pressure_series: list[TimePoint] = []
    running_series: list[TimePoint] = []
    cap_series: list[TimePoint] = []
    annotations: list[dict[str, object]] = []

    running_count = 0
    total_starts = 0
    successful = 0
    failed = 0
    durations: list[float] = []
    successful_durations: list[float] = []
    exit_timestamps: list[str] = []  # timestamps of successful exits, for hourly bucketing
    peak_rss: list[int] = []
    adjustments = 0
    kill_reasons: dict[str, int] = {}

    # Failure counts by class
    rate_limited = 0
    server_error = 0
    timeout = 0
    invalid_output = 0
    crash = 0
    unknown_failures = 0

    first_ts: str | None = None
    last_ts: str | None = None

    max_per_annotation_type = 30

    kill_annotations: list[dict[str, object]] = []
    throttle_annotations: list[dict[str, object]] = []
    failed_exit_annotations: list[dict[str, object]] = []

    for ev in events:
        ts_str = ev.ts.isoformat()
        if first_ts is None:
            first_ts = ts_str
        last_ts = ts_str

        if isinstance(ev, PoolStartEvent):
            metadata["pool_id"] = ev.pool_id
            metadata["backend"] = ev.backend
            metadata["max_concurrency"] = ev.max_concurrency
            cap_series.append(TimePoint(x=ts_str, y=ev.max_concurrency))

        elif isinstance(ev, PoolShutdownEvent):
            metadata["completed"] = ev.completed
            metadata["failed"] = ev.failed
            metadata["killed"] = ev.killed

        elif isinstance(ev, ProcessStartEvent):
            total_starts += 1
            running_count += 1
            running_series.append(TimePoint(x=ts_str, y=running_count))
            _inc(taxonomy, "process/start")

        elif isinstance(ev, ProcessExitEvent):
            running_count = max(0, running_count - 1)
            running_series.append(TimePoint(x=ts_str, y=running_count))

            if ev.exit_code == 0:
                successful += 1
                _inc(taxonomy, "process/exit/success")
                successful_durations.append(ev.elapsed_s)
                exit_timestamps.append(ts_str)
            else:
                failed += 1
                _inc(taxonomy, "process/exit/failed")
                elapsed_str = f" after {ev.elapsed_s:.0f}s" if ev.elapsed_s else ""
                failed_exit_annotations.append(
                    {
                        "x": ts_str,
                        "label": f"Exit({ev.exit_code}): {ev.label}{elapsed_str}",
                        "color": "var(--chart-series-error)",
                    }
                )

                # Classify failure
                fc = ev.failure_class
                if fc == "rate_limited":
                    rate_limited += 1
                elif fc == "server_error":
                    server_error += 1
                elif fc == "timeout":
                    timeout += 1
                elif fc == "invalid_output":
                    invalid_output += 1
                elif fc == "crash":
                    crash += 1
                elif fc is not None:
                    unknown_failures += 1

            durations.append(ev.elapsed_s)
            if ev.peak_rss_bytes is not None:
                peak_rss.append(ev.peak_rss_bytes)

        elif isinstance(ev, ProcessKillEvent):
            running_count = max(0, running_count - 1)
            running_series.append(TimePoint(x=ts_str, y=running_count))
            _inc(taxonomy, f"process/kill/{ev.kill_reason}")
            kill_reasons[ev.kill_reason] = kill_reasons.get(ev.kill_reason, 0) + 1
            if ev.peak_rss_bytes is not None:
                peak_rss.append(ev.peak_rss_bytes)
            kill_annotations.append(
                {
                    "x": ts_str,
                    "label": f"Kill: {ev.label} ({ev.kill_reason})",
                    "color": "var(--chart-series-error)",
                }
            )

        elif isinstance(ev, ConcurrencyAdjustEvent):
            adjustments += 1
            cap_series.append(TimePoint(x=ts_str, y=ev.new))
            _inc(taxonomy, "concurrency/adjust")
            throttle_annotations.append(
                {
                    "x": ts_str,
                    "label": f"Throttle: {ev.old}\u2192{ev.new}"
                    + (f" ({ev.reason})" if ev.reason else ""),
                    "color": "var(--chart-series-warning)",
                }
            )

        elif isinstance(ev, PressureCheckEvent):  # pyright: ignore[reportUnnecessaryIsInstance]
            pressure_series.append(TimePoint(x=ts_str, y=ev.available_pct))
            _inc(taxonomy, f"pressure/{ev.level}")

    # Build annotations (capped per type to avoid chart clutter)
    if len(kill_annotations) <= max_per_annotation_type:
        annotations.extend(kill_annotations)
    if len(throttle_annotations) <= max_per_annotation_type:
        annotations.extend(throttle_annotations)
    if len(failed_exit_annotations) <= max_per_annotation_type:
        annotations.extend(failed_exit_annotations)

    # Duration metadata
    if first_ts and last_ts:
        try:
            t0 = datetime.fromisoformat(first_ts)
            t1 = datetime.fromisoformat(last_ts)
            delta = t1 - t0
            total_s = int(delta.total_seconds())
            hours = total_s // 3600
            mins = (total_s % 3600) // 60
            metadata["duration"] = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
        except (ValueError, TypeError):
            pass

    return RunPoolAnalysis(
        taxonomy_counts=taxonomy,
        metadata=metadata,
        pressure_series=pressure_series,
        running_count_series=running_series,
        concurrency_cap_series=cap_series,
        total_process_runs=total_starts,
        successful_exits=successful,
        failed_exits=failed,
        exit_durations_s=durations,
        successful_exit_durations_s=successful_durations,
        successful_exit_timestamps=exit_timestamps,
        peak_rss_values=peak_rss,
        failure_counts=FailureCounts(
            rate_limited=rate_limited,
            server_error=server_error,
            timeout=timeout,
            invalid_output=invalid_output,
            crash=crash,
            unknown=unknown_failures,
        ),
        concurrency_adjustments=adjustments,
        kill_reasons=kill_reasons,
        annotations=annotations,
    )


def _inc(d: dict[str, int], key: str) -> None:
    d[key] = d.get(key, 0) + 1
