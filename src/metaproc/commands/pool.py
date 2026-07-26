"""metaproc pool — inspect RunPool status and event logs."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from prettyfmt import fmt_timedelta

from metaproc import paths as paths_mod
from metaproc.cli import app, get_output
from metaproc.io import (
    artifact_exists,
    iter_artifact_paths,
    iter_jsonl_objects,
    resolve_existing_artifact,
)
from metaproc.io.state_io import read_status_at, write_status_at
from metaproc.models.runtime import StatusRecord
from metaproc.paths import LOGS_DIR, POOL_STATUS_FILE, SCALE_OVERRIDE_FILE, STATE_DIR
from metaproc.runpool.host_admission import (
    DEFAULT_HOST_ADMISSION_NAMESPACE,
    list_host_admission_slots,
)
from metaproc.runpool.status import ScaleOverride, is_pool_alive, read_status, write_scale_override

_COMPOSITE_WALK_MAX_DEPTH = 4
"""Cap how deep `_iter_composite_run_dirs` recurses.

Composite nesting in production is one or two levels (e.g. a dispatch parent
runs a child process, which in turn fans out per-item). Four levels gives
headroom for nested composites without scanning unrelated subtrees.
"""

pool_app = typer.Typer(
    name="pool",
    help="Inspect RunPool status and event logs.",
    no_args_is_help=True,
)
app.add_typer(pool_app)


def _format_concurrency_plan_summary(plan: object) -> str:
    """Return a compact operator-facing ConcurrencyPlan summary."""
    if plan is None:
        return ""
    if isinstance(plan, Mapping):
        data = plan
    else:
        model_dump = getattr(plan, "model_dump", None)
        if not callable(model_dump):
            return ""
        data = model_dump(mode="json")
        if not isinstance(data, Mapping):
            return ""

    rss_bytes = _int_or_none(data.get("estimated_process_rss_bytes"))
    rss_mb = "-" if rss_bytes is None else str(rss_bytes // (1024 * 1024))
    budget = _float_or_none(data.get("initial_memory_budget_fraction"))
    budget_text = "-" if budget is None else f"{budget:.2f}"
    return (
        f"profile={_display_plan_value(data.get('execution_profile'))} "
        f"backend={_display_plan_value(data.get('backend'))} "
        f"requested={_display_plan_value(data.get('requested_max_concurrency'))} "
        f"configured={_display_plan_value(data.get('configured_max_concurrency'))} "
        f"initial={_display_plan_value(data.get('initial_concurrency'))} "
        f"limit={_display_plan_value(data.get('limiting_factor'))} "
        f"host={_display_plan_value(data.get('host_max_concurrency'))} "
        f"rss={rss_mb}MB "
        f"budget={budget_text}"
    )


def _display_plan_value(value: object) -> str:
    return "-" if value is None or value == "" else str(value)


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


@pool_app.command("status")
def pool_status(
    run_dir: Path = typer.Argument(..., help="Run directory"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show RunPool status for a run directory.

    Walks composite child run directories (e.g. ``<parent>/child-process/``)
    and renders one block per pool found. When
    no pool file exists at the parent level and no child pools are found
    either, the output distinguishes "missing pool file" from "no active
    pool anywhere under the composite run". See ``internal issue``.
    """
    out = get_output()

    entries = _composite_pool_status_files(run_dir)

    if as_json:
        payload = [
            {
                "run_dir": str(entry["run_dir"]),
                "step_id": entry.get("step_id"),
                "status_file": str(entry["status_file"]),
                "status": (
                    entry["status"].model_dump(mode="json") if entry["status"] is not None else None
                ),
                "error": entry.get("error"),
            }
            for entry in entries
        ]
        out.data(json.dumps(payload, indent=2))
        return

    if not entries:
        out.progress(
            f"No pool status file in {paths_mod.run_state_dir(run_dir)} "
            f"and no composite child pools under {run_dir}"
        )
        raise typer.Exit(code=0)

    for idx, entry in enumerate(entries):
        if idx > 0:
            out.data("")
        entry_run_dir = entry["run_dir"]
        step_id = entry.get("step_id")
        if entry_run_dir != run_dir or step_id:
            try:
                rel = entry_run_dir.relative_to(run_dir)
            except ValueError:
                rel = Path(entry_run_dir.name)
            header = str(rel)
            if step_id:
                header = f"{rel}/{step_id}" if rel != Path() else step_id
            out.data(f"[{header}]")
        if entry["status"] is None:
            out.data(f"  ERROR: {entry.get('error') or 'failed to read status'}")
            continue
        _render_pool_status_block(entry["status"], out=out)


def _render_pool_status_block(status: Any, *, out: Any) -> None:
    """Render one pool's status block. Extracted so multiple sub-pools
    discovered under a composite run can be rendered in sequence."""
    alive = is_pool_alive(status)
    alive_str = "ALIVE" if alive else "STOPPED"

    out.data(f"Pool: {status.pool_id} ({alive_str})")
    out.data(f"Backend: {status.backend}")
    out.data(
        f"Concurrency: {status.current_concurrency}/{status.max_concurrency} "
        f"(active={status.active_count}, pending={status.pending_count})"
    )
    plan_summary = _format_concurrency_plan_summary(status.concurrency_plan)
    if plan_summary:
        out.data(f"Plan: {plan_summary}")
    results_line = (
        f"Results: {status.completed_count} completed, "
        f"{status.failed_count} failed, {status.killed_count} killed"
    )
    if status.pending_retries > 0:
        results_line += f", {status.pending_retries} retries pending"
    out.data(results_line)
    out.data(
        f"Pressure: {status.pressure.level.value} "
        f"(swap={status.pressure.swap_level.value}, "
        f"disk={status.pressure.disk_level.value}, "
        f"{status.pressure.available_pct:.0f}% available)"
    )
    disk_free = (
        f"{status.pressure.disk_free_gb:.1f}GB"
        if status.pressure.disk_free_gb is not None
        else "unknown"
    )
    health_line = (
        "Health: "
        f"RAM={_format_float(status.pressure.total_memory_gb, suffix='GB')} "
        f"swap={status.pressure.swap_used_gb:.1f}GB "
        f"swap_delta={status.pressure.swap_delta_gb_per_min:.1f}GB/min "
        f"swap_level={status.pressure.swap_level.value} "
        f"disk_free={disk_free} "
        f"disk={status.pressure.disk_level.value} "
        f"disk_cause={status.pressure.disk_pressure_cause}"
    )
    out.data(health_line)

    if status.processes:
        out.data("Active processes:")
        for proc in status.processes:
            elapsed = fmt_timedelta(proc.elapsed_s)
            rss = f" RSS={proc.rss_bytes // (1024 * 1024)}MB" if proc.rss_bytes else ""
            out.data(f"  {proc.label}: PID={proc.pid} ({elapsed}{rss})")

    if status.recent_completions:
        out.data("Recent completions:")
        for proc in status.recent_completions[-5:]:
            elapsed = fmt_timedelta(proc.elapsed_s)
            suffix = ""
            if proc.kill_reason:
                suffix = f" (killed: {proc.kill_reason})"
            elif proc.exit_code and proc.exit_code != 0:
                suffix = f" (exit={proc.exit_code})"
            lane_tag = f" [lane={proc.lane_id}]" if proc.lane_id else ""
            out.data(f"  {proc.label}: {proc.status}{suffix}{lane_tag} ({elapsed})")

    # Lane breakdown — emitted whenever the pool tracks any lane (even the
    # degenerate single-lane case) so operators get a uniform reading path
    # for comparison runpools.
    lanes = getattr(status, "lanes", None) or []
    if lanes:
        out.data("Lanes:")
        for lane in lanes:
            profile = lane.execution_profile or "-"
            role = f" role={lane.comparison_role}" if lane.comparison_role else ""
            replica = f" r{lane.replica_index}" if lane.replica_index else ""
            out.data(
                f"  {lane.lane_id} (profile={profile}{replica}{role}): "
                f"active={lane.active_count}, "
                f"completed={lane.completed_count}, "
                f"failed={lane.failed_count}, "
                f"killed={lane.killed_count}"
            )


@pool_app.command("events")
def pool_events(
    run_dir: Path = typer.Argument(..., help="Run directory"),
    limit: int = typer.Option(20, "--limit", help="Max events to show"),
    event_type: str | None = typer.Option(None, "--type", help="Filter by event type"),  # noqa: UP007
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
    summary: bool = typer.Option(
        False,
        "--summary",
        help="Aggregate counts (per event type, or per-label/classification when --type=auth_outcome, or per-label/policy when --type=auth_lease_acquired) instead of a per-event listing",
    ),
) -> None:
    """Show RunPool event log for a run directory."""
    out = get_output()

    event_files = _runpool_event_files_for_read(run_dir)
    if not event_files:
        out.progress(f"No RunPool event files under {paths_mod.run_logs_dir(run_dir)}")
        raise typer.Exit(code=0)

    events = _read_runpool_events(event_files)
    total_in_file = len(events)

    if event_type:
        events = [e for e in events if e.get("event") == event_type]

    if summary:
        report = _summarize_events(events, total_in_file=total_in_file, event_type=event_type)
        if as_json:
            out.data(json.dumps(report, indent=2))
        else:
            _render_summary(report, out=out)
        return

    # Show last N events.
    events = events[-limit:]

    if as_json:
        out.data(json.dumps(events, indent=2))
        return

    for ev in events:
        ts = ev.get("ts", "")[:19]
        event_name = ev.get("event", "unknown")
        label = ev.get("label", "")
        pid = ev.get("pid", "")

        if event_name == "pool_start":
            line = (
                f"{ts}  pool_start  backend={ev.get('backend')} "
                f"max={ev.get('max_concurrency')} initial={ev.get('initial_concurrency')}"
            )
            plan_summary = _format_concurrency_plan_summary(ev.get("concurrency_plan"))
            if plan_summary:
                line += f" plan=({plan_summary})"
            out.data(line)
        elif event_name == "pool_shutdown":
            out.data(
                f"{ts}  pool_shutdown  completed={ev.get('completed')} "
                f"failed={ev.get('failed')} killed={ev.get('killed')}"
            )
        elif event_name == "process_start":
            out.data(f"{ts}  process_start  {label} PID={pid}")
        elif event_name == "process_exit":
            out.data(
                f"{ts}  process_exit  {label} PID={pid} "
                f"exit={ev.get('exit_code')} ({ev.get('elapsed_s', 0):.1f}s)"
            )
        elif event_name == "process_kill":
            out.data(f"{ts}  process_kill  {label} PID={pid} reason={ev.get('kill_reason')}")
        elif event_name == "concurrency_adjust":
            details = []
            if ev.get("effective_target") is not None:
                details.append(f"target={ev.get('effective_target')}")
            if ev.get("memory_ceiling") is not None:
                details.append(f"mem={ev.get('memory_ceiling')}")
            if ev.get("provider_ceiling") is not None:
                details.append(f"provider={ev.get('provider_ceiling')}")
            if ev.get("bottleneck") is not None:
                details.append(f"bottleneck={ev.get('bottleneck')}")
            detail = f" {' '.join(details)}" if details else ""
            out.data(
                f"{ts}  concurrency  {ev.get('old')} -> {ev.get('new')} "
                f"({ev.get('reason')}){detail}"
            )
        elif event_name == "pressure_check":
            rss = ev.get("active_rss_bytes")
            rss_mb = f" rss={int(rss) // (1024 * 1024)}MB" if isinstance(rss, int) else ""
            memory_level = (
                f" memory={ev.get('memory_level')}"
                if ev.get("memory_level") not in (None, "")
                else ""
            )
            swap = (
                f" swap={ev.get('swap_used_gb'):.1f}GB"
                if isinstance(ev.get("swap_used_gb"), (int, float))
                else ""
            )
            swap_level = (
                f" swap_level={ev.get('swap_level')}"
                if ev.get("swap_level") not in (None, "")
                else ""
            )
            disk = (
                f" disk_free={ev.get('disk_free_gb'):.1f}GB"
                if isinstance(ev.get("disk_free_gb"), (int, float))
                else ""
            )
            disk_cause = (
                f" disk_cause={ev.get('disk_pressure_cause')}"
                if ev.get("disk_pressure_cause") not in (None, "none")
                else ""
            )
            target = (
                f" cap={ev.get('current_concurrency')}/{ev.get('effective_target')}"
                if ev.get("current_concurrency") is not None
                and ev.get("effective_target") is not None
                else ""
            )
            active = (
                f" active={ev.get('active_count')}" if ev.get("active_count") is not None else ""
            )
            bottleneck = (
                f" bottleneck={ev.get('bottleneck')}" if ev.get("bottleneck") is not None else ""
            )
            out.data(
                f"{ts}  pressure  level={ev.get('level')} "
                f"available={ev.get('available_pct'):.0f}%"
                f"{memory_level}{swap}{swap_level}{disk}{disk_cause}"
                f"{target}{active}{rss_mb}{bottleneck}"
            )
        elif event_name == "retry_scheduled":
            out.data(
                f"{ts}  retry_scheduled  {label} "
                f"attempt={ev.get('attempt')} backoff={ev.get('backoff_s')}s"
            )
        else:
            out.data(f"{ts}  {event_name}  {json.dumps(ev)}")


@pool_app.command("host-slots")
def pool_host_slots(
    namespace: str = typer.Option(
        DEFAULT_HOST_ADMISSION_NAMESPACE,
        "--namespace",
        help="Host admission namespace to inspect",
    ),
    root_dir: Path | None = typer.Option(  # noqa: UP007
        None,
        "--root-dir",
        help="Host admission root directory; defaults to ~/.metaproc/runpool/host-slots",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show host-wide local RunPool admission slots.

    This is the supported operator view for the disk-backed host admission gate.
    It is read-only and does not reclaim or mutate stale leases.
    """
    out = get_output()
    slots = list_host_admission_slots(root_dir=root_dir, namespace=namespace)
    rows = [slot.as_dict() for slot in slots]

    if as_json:
        out.data(json.dumps(rows, indent=2))
        return

    if not slots:
        root = root_dir or Path.home() / ".metaproc" / "runpool" / "host-slots"
        out.progress(f"No host admission slots under {root / namespace}")
        return

    alive = sum(1 for slot in slots if slot.status == "alive")
    stale = len(slots) - alive
    out.data(f"Host admission namespace: {namespace}")
    out.data(f"Slots: {alive} alive, {stale} stale")
    for slot in slots:
        age = fmt_timedelta(slot.age_s)
        limit = slot.limit if slot.limit is not None else "?"
        out.data(
            f"  slot={slot.slot_id} status={slot.status} limit={limit} "
            f"label={slot.label or '-'} child_pid={slot.child_pid or '-'} "
            f"owner_pid={slot.owner_pid or '-'} age={age} pool={slot.pool_id or '-'}"
        )


@pool_app.command("health")
def pool_health(
    run_dir: Path = typer.Argument(..., help="Run directory"),
    limit: int = typer.Option(20, "--limit", help="Max health samples to show"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
    summary: bool = typer.Option(False, "--summary", help="Show aggregate health summary"),
) -> None:
    """Show dedicated RunPool health samples for a run directory."""
    out = get_output()

    health_files = _runpool_health_files_for_read(run_dir)
    if not health_files:
        out.progress(f"No RunPool health files under {paths_mod.run_logs_dir(run_dir)}")
        raise typer.Exit(code=0)

    samples = _read_runpool_events(health_files)
    if summary:
        report = _summarize_health(samples)
        if as_json:
            out.data(json.dumps(report, indent=2))
        else:
            _render_health_summary(report, out=out)
        return

    samples = samples[-limit:]
    if as_json:
        out.data(json.dumps(samples, indent=2))
        return

    for sample in samples:
        ts = sample.get("ts", "")[:19]
        line = (
            f"{ts}  health  level={sample.get('level')} "
            f"memory={sample.get('memory_level')} "
            f"swap_level={sample.get('swap_level')} "
            f"available={sample.get('available_pct'):.0f}% "
            f"swap={sample.get('swap_used_gb'):.1f}GB "
            f"swap_delta={sample.get('swap_delta_gb_per_min'):.1f}GB/min "
            f"disk_free={sample.get('disk_free_gb'):.1f}GB "
            f"cap={sample.get('current_concurrency')}/{sample.get('effective_target')} "
            f"active={sample.get('active_count')}"
        )
        out.data(line)


def _summarize_events(
    events: list[dict[str, Any]],
    *,
    total_in_file: int,
    event_type: str | None,
) -> dict[str, Any]:
    """Build a summary report from a (possibly type-filtered) event list.

    When ``event_type=auth_outcome`` the summary specializes: per-label,
    per-classification, retry distribution, fallback-policy distribution,
    rotation count. Otherwise it returns counts by event type.
    """
    if not events:
        return {
            "total_in_file": total_in_file,
            "filter": {"event_type": event_type},
            "matched": 0,
        }

    first_ts = events[0].get("ts", "")
    last_ts = events[-1].get("ts", "")
    base: dict[str, Any] = {
        "total_in_file": total_in_file,
        "filter": {"event_type": event_type},
        "matched": len(events),
        "first_ts": first_ts,
        "last_ts": last_ts,
    }

    if event_type == "auth_outcome":
        labels: Counter[str] = Counter()
        classifications: Counter[str] = Counter()
        retries: Counter[int] = Counter()
        fallback_policies: Counter[str] = Counter()
        per_label_classification: dict[str, Counter[str]] = {}
        # HTTP-axis rollups — leading indicators of imminent cohort loss.
        api_statuses: Counter[str] = Counter()
        oauth_refresh_statuses: Counter[str] = Counter()
        per_label_oauth_refresh: dict[str, Counter[str]] = {}
        rotated = 0
        for ev in events:
            label = ev.get("label", "")
            classification = ev.get("classification", "")
            labels[label] += 1
            classifications[classification] += 1
            retries[int(ev.get("retry_count", 0) or 0)] += 1
            fallback_policies[ev.get("fallback_policy", "") or ""] += 1
            if ev.get("rotated"):
                rotated += 1
            per_label_classification.setdefault(label, Counter())[classification] += 1
            api_status = ev.get("api_status")
            if api_status is not None:
                api_statuses[str(api_status)] += 1
            oauth_status = ev.get("oauth_refresh_status")
            if oauth_status is not None:
                oauth_refresh_statuses[str(oauth_status)] += 1
                per_label_oauth_refresh.setdefault(label, Counter())[str(oauth_status)] += 1
        base["auth_outcome"] = {
            "by_label": dict(labels),
            "by_classification": dict(classifications),
            "by_label_classification": {
                lbl: dict(c) for lbl, c in per_label_classification.items()
            },
            "retry_distribution": dict(sorted(retries.items())),
            "fallback_policy": dict(fallback_policies),
            "rotated": rotated,
            "by_api_status": dict(api_statuses),
            "by_oauth_refresh_status": dict(oauth_refresh_statuses),
            "by_label_oauth_refresh": {lbl: dict(c) for lbl, c in per_label_oauth_refresh.items()},
        }
        return base

    if event_type == "auth_lease_acquired":
        # Phase 1 verifiability hook (plan-2026-05-03 internal issue).
        # Schema-v2 acquisition-time companion to auth_outcome —
        # by-label distribution lets operators verify ROUND_ROBIN
        # actually distributed in production before the full Phase 2
        # aggregator (auth_usage) lands.
        lease_labels: Counter[str] = Counter()
        policies: Counter[str] = Counter()
        per_label_policy: dict[str, Counter[str]] = {}
        for ev in events:
            label = ev.get("label", "")
            policy = ev.get("policy", "")
            lease_labels[label] += 1
            policies[policy or "(unset)"] += 1
            per_label_policy.setdefault(label, Counter())[policy or "(unset)"] += 1
        base["auth_lease_acquired"] = {
            "by_label": dict(lease_labels),
            "by_policy": dict(policies),
            "by_label_policy": {lbl: dict(c) for lbl, c in per_label_policy.items()},
        }
        return base

    if event_type == "pressure_check":
        base["pressure_check"] = _summarize_pressure_samples(events)
        return base

    by_event: Counter[str] = Counter()
    for ev in events:
        by_event[ev.get("event", "unknown")] += 1
    base["by_event"] = dict(by_event)
    return base


def _summarize_health(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an aggregate report from dedicated runpool health samples."""
    if not samples:
        return {"matched": 0}

    summary = _summarize_pressure_samples(samples)

    return {
        "matched": len(samples),
        "first_ts": samples[0].get("ts", ""),
        "last_ts": samples[-1].get("ts", ""),
        **summary,
    }


def _summarize_pressure_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate memory, swap, disk, and pool fields from pressure-like samples."""

    def _numbers(key: str) -> list[float]:
        return [
            float(sample[key]) for sample in samples if isinstance(sample.get(key), (int, float))
        ]

    available = _numbers("available_pct")
    swap = _numbers("swap_used_gb")
    swap_delta = _numbers("swap_delta_gb_per_min")
    disk_free = _numbers("disk_free_gb")
    active = _numbers("active_count")
    concurrency = _numbers("current_concurrency")
    memory_levels = Counter(str(sample.get("memory_level", "unknown")) for sample in samples)
    swap_levels = Counter(str(sample.get("swap_level", "unknown")) for sample in samples)
    disk_causes = Counter(str(sample.get("disk_pressure_cause", "none")) for sample in samples)
    positive_swap_delta = [value for value in swap_delta if value > 0]

    return {
        "levels": dict(Counter(str(sample.get("level", "unknown")) for sample in samples)),
        "memory_levels": dict(memory_levels),
        "swap_levels": dict(swap_levels),
        "min_available_pct": min(available) if available else None,
        "max_available_pct": max(available) if available else None,
        "max_swap_used_gb": max(swap) if swap else None,
        "max_swap_delta_gb_per_min": max(swap_delta) if swap_delta else None,
        "positive_swap_delta_samples": len(positive_swap_delta),
        "min_disk_free_gb": min(disk_free) if disk_free else None,
        "disk_pressure_causes": dict(disk_causes),
        "max_active_count": int(max(active)) if active else None,
        "max_concurrency": int(max(concurrency)) if concurrency else None,
    }


def _render_health_summary(report: dict[str, Any], *, out: Any) -> None:
    """Render a health summary as plain text."""
    out.data(f"Health samples: {report.get('matched', 0)}")
    if not report.get("matched"):
        return
    out.data(f"Window: {report['first_ts']}  →  {report['last_ts']}")
    out.data(f"Levels: {report.get('levels', {})}")
    out.data(f"Memory levels: {report.get('memory_levels', {})}")
    out.data(f"Swap levels: {report.get('swap_levels', {})}")
    memory_line = (
        "Memory: "
        f"available min={_format_float(report.get('min_available_pct'), suffix='%')} "
        f"max={_format_float(report.get('max_available_pct'), suffix='%')} "
        f"swap max={_format_float(report.get('max_swap_used_gb'), suffix='GB')} "
        f"swap_delta max={_format_float(report.get('max_swap_delta_gb_per_min'), suffix='GB/min')} "
        f"swap_growth_samples={report.get('positive_swap_delta_samples')}"
    )
    out.data(memory_line)
    disk_line = (
        "Disk/pool: "
        f"disk_free min={_format_float(report.get('min_disk_free_gb'), suffix='GB')} "
        f"disk_causes={report.get('disk_pressure_causes', {})} "
        f"active max={report.get('max_active_count')} "
        f"cap max={report.get('max_concurrency')}"
    )
    out.data(disk_line)


def _render_summary(report: dict[str, Any], *, out: Any) -> None:
    """Render a summary report as plain text."""
    matched = report.get("matched", 0)
    total = report.get("total_in_file", 0)
    filter_type = (report.get("filter") or {}).get("event_type")
    filter_str = f" (type={filter_type})" if filter_type else ""
    out.data(f"Events: {matched}/{total} matched{filter_str}")
    if matched == 0:
        return
    out.data(f"Window: {report['first_ts']}  →  {report['last_ts']}")

    if "auth_outcome" in report:
        ao = report["auth_outcome"]
        out.data("\nBy label:")
        for label, n in sorted(ao["by_label"].items(), key=lambda kv: -kv[1]):
            out.data(f"  {label or '(none)':12s}  {n}")
        out.data("\nBy classification:")
        for cls, n in sorted(ao["by_classification"].items(), key=lambda kv: -kv[1]):
            out.data(f"  {cls or '(none)':12s}  {n}")
        if any(c for c in ao["by_classification"] if c and c != "ok"):
            out.data("\nLabel × classification (non-ok detail):")
            for label, by_cls in sorted(ao["by_label_classification"].items()):
                non_ok = {k: v for k, v in by_cls.items() if k != "ok"}
                if non_ok:
                    out.data(f"  {label}: {non_ok}")
        out.data("\nRetry distribution:")
        for retries, n in ao["retry_distribution"].items():
            out.data(f"  retry={retries}  {n}")
        out.data(f"\nRotations: {ao['rotated']}")
        out.data(f"Fallback policy: {ao['fallback_policy']}")
        # HTTP-axis rollups: leading indicators of upcoming cohort loss
        # (e.g. an uptick in oauth_refresh_status=400 on a label is the
        # signal that refresh tokens are rotating server-side faster
        # than the pool can capture).
        if ao.get("by_api_status"):
            out.data("\nBy api_status:")
            for status, n in sorted(ao["by_api_status"].items(), key=lambda kv: -kv[1]):
                out.data(f"  {status:6s}  {n}")
        if ao.get("by_oauth_refresh_status"):
            out.data("\nBy oauth_refresh_status:")
            for status, n in sorted(ao["by_oauth_refresh_status"].items(), key=lambda kv: -kv[1]):
                out.data(f"  {status:6s}  {n}")
            non_ok_oauth = {
                lbl: dict(c)
                for lbl, c in ao.get("by_label_oauth_refresh", {}).items()
                if any(int(s) >= 400 for s in c)
            }
            if non_ok_oauth:
                out.data("\nLabel × oauth_refresh_status (failures only):")
                for lbl, by_status in sorted(non_ok_oauth.items()):
                    out.data(f"  {lbl}: {by_status}")
        return

    if "auth_lease_acquired" in report:
        # Phase 1 verifiability hook — render by-label acquisition
        # counts so operators can sanity-check ROUND_ROBIN distribution
        # before the full Phase 2 aggregator lands.
        ala = report["auth_lease_acquired"]
        out.data("\nBy label (acquisitions):")
        total = sum(ala["by_label"].values()) or 1
        for label, n in sorted(ala["by_label"].items(), key=lambda kv: -kv[1]):
            pct = 100 * n / total
            out.data(f"  {label or '(none)':12s}  {n}  ({pct:.1f}%)")
        out.data("\nBy policy:")
        for policy, n in sorted(ala["by_policy"].items(), key=lambda kv: -kv[1]):
            out.data(f"  {policy:14s}  {n}")
        return

    if "pressure_check" in report:
        pressure = report["pressure_check"]
        out.data(f"\nLevels: {pressure.get('levels', {})}")
        out.data(f"Memory levels: {pressure.get('memory_levels', {})}")
        out.data(f"Swap levels: {pressure.get('swap_levels', {})}")
        out.data(
            "Swap: "
            f"max={_format_float(pressure.get('max_swap_used_gb'), suffix='GB')} "
            f"delta_max={_format_float(pressure.get('max_swap_delta_gb_per_min'), suffix='GB/min')} "
            f"growth_samples={pressure.get('positive_swap_delta_samples')}"
        )
        out.data(
            "Pool: "
            f"active max={pressure.get('max_active_count')} "
            f"cap max={pressure.get('max_concurrency')}"
        )
        return

    out.data("\nBy event type:")
    for event_name, n in sorted(report.get("by_event", {}).items(), key=lambda kv: -kv[1]):
        out.data(f"  {event_name:20s}  {n}")


def _format_float(value: object, *, suffix: str = "", digits: int = 1) -> str:
    """Format optional numeric report values for operator-facing summaries."""
    if not isinstance(value, (int, float)):
        return "unknown"
    return f"{float(value):.{digits}f}{suffix}"


@pool_app.command("concurrency-timeline")
def pool_concurrency_timeline(
    run_dir: Path = typer.Argument(..., help="Run directory"),
    bucket_minutes: int = typer.Option(
        0,
        "--bucket-minutes",
        help="Group adjustments into N-minute buckets (showing min/max/last per bucket); 0 = list every adjustment",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show how the pool's concurrency cap evolved over the run.

    Reads ``concurrency_adjust`` events from run-level, step-scoped, and
    worker-scoped RunPool event streams and renders the trajectory so
    ramp-up validation is a one-liner instead of grepping raw JSONL.
    Anchored against the first ``pool_start`` timestamp so each
    adjustment carries a relative offset.
    """
    out = get_output()
    event_files = _runpool_event_files_for_read(run_dir)
    if not event_files:
        out.progress(f"No RunPool event files under {paths_mod.run_logs_dir(run_dir)}")
        raise typer.Exit(code=0)
    events = _read_runpool_events(event_files)
    timeline = _build_concurrency_timeline(events, bucket_minutes=bucket_minutes)
    if as_json:
        out.data(json.dumps(timeline, indent=2))
        return
    _render_concurrency_timeline(timeline, out=out)


def _resolve_event_path(*, is_v2: bool, v2_path: Path, legacy_path: Path) -> Path:
    """Resolve one event stream using the V2 marker and exact legacy fallback."""
    if is_v2 or artifact_exists(v2_path):
        return resolve_existing_artifact(v2_path)
    return resolve_existing_artifact(legacy_path)


def _iter_composite_run_dirs(run_dir: Path) -> Iterable[Path]:
    """Yield ``run_dir`` and every composite child run directory beneath it.

    A composite child run directory is any nested directory that owns its own
    ``.state/`` branch. A dispatch parent that fans out to a child process is
    the canonical case: the parent has a ``.state/`` and
    a ``.logs/`` at the top, and each child step that runs through a
    sub-process gets ``<parent>/<step_id>/.state/`` of its own.

    The walk:

    - always yields ``run_dir`` first (even if it has no ``.state/``), so
      callers preserve the existing single-run path when there is no
      composite nesting;
    - is depth-capped at :data:`_COMPOSITE_WALK_MAX_DEPTH` to avoid scanning
      unbounded subtrees;
    - never descends into ``.state/``, ``.logs/``, or ``worker-*`` /
      ``slot-*`` directories (those are runpool internals, not nested runs);
    - dedupes by resolved path so symlinked composite mounts don't double-up.

    This is the shared discovery helper called out in
    ``internal issue`` — extracting it collapsed three near-identical walkers
    (pool events, pool health, pool rollup) onto one source of truth.
    """
    yield run_dir
    if not run_dir.is_dir():
        return
    seen: set[Path] = set()
    try:
        seen.add(run_dir.resolve())
    except OSError:
        return

    def _walk(parent: Path, depth: int) -> Iterable[Path]:
        if depth > _COMPOSITE_WALK_MAX_DEPTH:
            return
        try:
            children = sorted(parent.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            name = child.name
            if name in {STATE_DIR, LOGS_DIR}:
                continue
            if name.startswith(("worker-", "slot-")):
                continue
            if name.startswith("."):
                continue
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            if (child / STATE_DIR).is_dir():
                yield child
            yield from _walk(child, depth + 1)

    yield from _walk(run_dir, depth=1)


def _composite_pool_status_files(run_dir: Path) -> list[dict[str, Any]]:
    """Return one entry per ``runpool-status.yaml`` discovered under ``run_dir``.

    Entries are ordered by relative path so the operator sees the parent run's
    own pool (if any) first, then each composite child in deterministic order.
    Each entry is a flat dict: ``run_dir``, ``status_file``, ``status``
    (``PoolStatus`` or ``None`` on parse failure), and an optional ``error``
    message — matches the shape consumed by both the JSON and text renderers.

    Discovery walks two levels for each run directory found by
    :func:`_iter_composite_run_dirs`:

    1. **Run-level pool**: ``<run>/.state/runpool-status.yaml``
    2. **Per-step pools**: ``<run>/.state/steps/<step>/runpool-status.yaml``

    This ensures composite-parent runs whose child pools live under
    ``.state/steps/`` (the canonical EIA dispatch layout) are surfaced by
    ``pool status`` rather than silently missed.
    """
    entries: list[dict[str, Any]] = []
    seen_status_files: set[Path] = set()

    def _try_add(sub_run: Path, status_file: Path, *, step_id: str | None = None) -> None:
        if status_file in seen_status_files:
            return
        seen_status_files.add(status_file)
        if not status_file.exists():
            return
        try:
            status = read_status(status_file)
        except Exception as exc:  # noqa: BLE001 — surface parse failures inline
            entries.append(
                {
                    "run_dir": sub_run,
                    "status_file": status_file,
                    "status": None,
                    "step_id": step_id,
                    "error": f"failed to read {status_file}: {exc}",
                }
            )
            return
        entries.append(
            {
                "run_dir": sub_run,
                "status_file": status_file,
                "status": status,
                "step_id": step_id,
                "error": None,
            }
        )

    for sub_run in _iter_composite_run_dirs(run_dir):
        # 1. Run-level pool.
        _try_add(sub_run, paths_mod.run_state_dir(sub_run) / POOL_STATUS_FILE)
        # 2. Per-step pools under .state/steps/<step_id>/.
        steps_state_root = paths_mod.run_state_dir(sub_run) / paths_mod.STEPS_SUBDIR
        if steps_state_root.is_dir():
            for step_dir in sorted(steps_state_root.iterdir()):
                if not step_dir.is_dir():
                    continue
                _try_add(sub_run, step_dir / POOL_STATUS_FILE, step_id=step_dir.name)
    return entries


def _runpool_event_files_for_one_run(run_dir: Path) -> list[Path]:
    """Return readable RunPool event streams for a single run directory.

    The operator-facing pool commands aggregate the run-level stream plus
    V2 step and worker streams. Unmarked runs keep exact legacy fallback
    per stream: a present V2 stream wins for that scope, otherwise the
    matching legacy stream is used.

    See :func:`_runpool_event_files_for_read` for the composite-aware
    aggregation across nested child run directories.
    """
    is_v2 = paths_mod.is_v2_run_layout(run_dir)
    candidates: list[Path] = [
        _resolve_event_path(
            is_v2=is_v2,
            v2_path=paths_mod.runpool_events(run_dir),
            legacy_path=paths_mod.legacy_runpool_events(run_dir),
        )
    ]

    step_ids: set[str] = set()
    steps_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
    if steps_root.is_dir():
        step_ids.update(
            path.parent.name
            for path in iter_artifact_paths(steps_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}")
        )
    legacy_steps_root = paths_mod.run_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
    if not is_v2 and legacy_steps_root.is_dir():
        step_ids.update(
            path.parent.name
            for path in iter_artifact_paths(legacy_steps_root, f"*/{paths_mod.POOL_EVENTS_FILE}")
        )
    for step_id in sorted(step_ids):
        candidates.append(
            _resolve_event_path(
                is_v2=is_v2,
                v2_path=paths_mod.runpool_step_events(run_dir, step_id),
                legacy_path=paths_mod.legacy_runpool_step_events(run_dir, step_id),
            )
        )

    worker_ids: set[str] = set()
    workers_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.WORKERS_SUBDIR
    if workers_root.is_dir():
        worker_ids.update(
            path.parent.name
            for path in iter_artifact_paths(workers_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}")
        )
    if not is_v2:
        worker_ids.update(
            path.parent.name
            for path in paths_mod.run_logs_dir(run_dir).glob(
                f"worker-*/{paths_mod.POOL_EVENTS_FILE}"
            )
        )
    for worker_id in sorted(worker_ids):
        candidates.append(
            _resolve_event_path(
                is_v2=is_v2,
                v2_path=paths_mod.runpool_worker_events(run_dir, worker_id),
                legacy_path=paths_mod.legacy_runpool_worker_events(run_dir, worker_id),
            )
        )

    seen: set[Path] = set()
    files: list[Path] = []
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        files.append(candidate)
    return files


def _runpool_event_files_for_read(run_dir: Path) -> list[Path]:
    """Composite-aware: return event streams across ``run_dir`` and every
    nested composite child run directory.

    See :func:`_iter_composite_run_dirs` for the discovery rules.
    """
    seen: set[Path] = set()
    files: list[Path] = []
    for sub_run in _iter_composite_run_dirs(run_dir):
        for candidate in _runpool_event_files_for_one_run(sub_run):
            if candidate in seen:
                continue
            seen.add(candidate)
            files.append(candidate)
    return files


def _runpool_health_files_for_one_run(run_dir: Path) -> list[Path]:
    """Return readable RunPool health streams for a single run directory."""
    candidates: list[Path] = [resolve_existing_artifact(paths_mod.runpool_health(run_dir))]

    steps_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
    if steps_root.is_dir():
        for path in iter_artifact_paths(steps_root, f"*/{paths_mod.RUNPOOL_HEALTH_FILE}"):
            candidates.append(path)

    workers_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.WORKERS_SUBDIR
    if workers_root.is_dir():
        for path in iter_artifact_paths(workers_root, f"*/{paths_mod.RUNPOOL_HEALTH_FILE}"):
            candidates.append(path)

    seen: set[Path] = set()
    files: list[Path] = []
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        files.append(candidate)
    return files


def _runpool_health_files_for_read(run_dir: Path) -> list[Path]:
    """Composite-aware: return health streams across ``run_dir`` and every
    nested composite child run directory."""
    seen: set[Path] = set()
    files: list[Path] = []
    for sub_run in _iter_composite_run_dirs(run_dir):
        for candidate in _runpool_health_files_for_one_run(sub_run):
            if candidate in seen:
                continue
            seen.add(candidate)
            files.append(candidate)
    return files


def _read_runpool_events(event_files: list[Path]) -> list[dict[str, Any]]:
    """Read and timestamp-sort raw RunPool event dicts from JSONL streams."""
    events: list[dict[str, Any]] = []
    for event_file in event_files:
        events.extend(iter_jsonl_objects(event_file))
    events.sort(key=lambda event: str(event.get("ts", "")))
    return events


def _parse_iso_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _build_concurrency_timeline(
    events: list[dict[str, Any]],
    *,
    bucket_minutes: int,
) -> dict[str, Any]:
    """Aggregate ``concurrency_adjust`` events into a trajectory.

    Returns a dict with the pool start info, the list of adjustments
    (or buckets, when ``bucket_minutes > 0``), and the final cap.
    """
    pool_start = next((e for e in events if e.get("event") == "pool_start"), None)
    adjustments = [e for e in events if e.get("event") == "concurrency_adjust"]
    final = (
        adjustments[-1].get("new")
        if adjustments
        else (pool_start.get("initial_concurrency") if pool_start else None)
    )
    base: dict[str, Any] = {
        "pool_start": (
            {
                "ts": pool_start.get("ts"),
                "backend": pool_start.get("backend"),
                "max_concurrency": pool_start.get("max_concurrency"),
                "initial_concurrency": pool_start.get("initial_concurrency"),
                "concurrency_plan": pool_start.get("concurrency_plan"),
            }
            if pool_start
            else None
        ),
        "adjustments_total": len(adjustments),
        "final_concurrency": final,
    }
    start_dt = _parse_iso_ts(pool_start.get("ts", "")) if pool_start else None

    def _offset(ts: str) -> str:
        ev_dt = _parse_iso_ts(ts)
        if not ev_dt or not start_dt:
            return ""
        delta = ev_dt - start_dt
        total = int(delta.total_seconds())
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"T+{h:02d}:{m:02d}:{s:02d}"

    if bucket_minutes <= 0:
        base["adjustments"] = [
            {
                "ts": e.get("ts"),
                "offset": _offset(e.get("ts", "")),
                "old": e.get("old"),
                "new": e.get("new"),
                "reason": e.get("reason"),
            }
            for e in adjustments
        ]
        return base

    bucket_seconds = bucket_minutes * 60
    buckets: dict[int, dict[str, Any]] = {}
    for ev in adjustments:
        ev_dt = _parse_iso_ts(ev.get("ts", ""))
        if not ev_dt:
            continue
        anchor = start_dt or ev_dt
        bucket_idx = int((ev_dt - anchor).total_seconds() // bucket_seconds)
        bucket_total_minutes = bucket_idx * bucket_minutes
        b_h, b_m = divmod(bucket_total_minutes, 60)
        bucket = buckets.setdefault(
            bucket_idx,
            {
                "bucket_idx": bucket_idx,
                "bucket_start_offset": f"T+{b_h:02d}:{b_m:02d}:00",
                "min": ev["new"],
                "max": ev["new"],
                "last": ev["new"],
                "adjustment_count": 0,
                "reasons": Counter(),
            },
        )
        bucket["min"] = min(bucket["min"], ev["new"])
        bucket["max"] = max(bucket["max"], ev["new"])
        bucket["last"] = ev["new"]
        bucket["adjustment_count"] += 1
        bucket["reasons"][ev.get("reason", "")] += 1

    base["bucket_minutes"] = bucket_minutes
    base["buckets"] = [{**b, "reasons": dict(b["reasons"])} for _, b in sorted(buckets.items())]
    return base


def _render_concurrency_timeline(timeline: dict[str, Any], *, out: Any) -> None:
    """Render a concurrency timeline as plain text."""
    ps = timeline.get("pool_start")
    if ps:
        line = (
            f"Pool start:  {ps['ts']}  backend={ps['backend']}  "
            f"max={ps['max_concurrency']}  initial={ps['initial_concurrency']}"
        )
        plan_summary = _format_concurrency_plan_summary(ps.get("concurrency_plan"))
        if plan_summary:
            line += f"  plan=({plan_summary})"
        out.data(line)
    out.data(f"Adjustments: {timeline['adjustments_total']}")
    out.data(f"Final:       {timeline['final_concurrency']}")
    if "buckets" in timeline:
        out.data(f"\nBuckets ({timeline['bucket_minutes']}m):")
        out.data(
            f"  {'offset':10s}  {'min':>4s}  {'max':>4s}  {'last':>4s}  {'count':>5s}  reasons"
        )
        for b in timeline["buckets"]:
            reasons = ", ".join(f"{k}:{v}" for k, v in b["reasons"].items())
            out.data(
                f"  {b['bucket_start_offset']:10s}  "
                f"{b['min']:>4d}  {b['max']:>4d}  {b['last']:>4d}  "
                f"{b['adjustment_count']:>5d}  {reasons}"
            )
        return
    if not timeline.get("adjustments"):
        return
    out.data("\nAdjustments:")
    for adj in timeline["adjustments"]:
        out.data(f"  {adj['offset']:11s}  {adj['old']:>3} -> {adj['new']:>3}  ({adj['reason']})")


@pool_app.command("rollup")
def pool_rollup(
    run_dir: Path = typer.Argument(
        ...,
        help="Run / process directory (walks down to find every sub-step pool)",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
    auth_outcomes: bool = typer.Option(
        False,
        "--auth-outcomes",
        help="Also aggregate auth_outcome events across every sub-pool (per-label/per-classification/per-step rollup)",
    ),
) -> None:
    """Roll up pool status (and optionally auth_outcome events) across every sub-step pool.

    Each fan-out step in a multi-step run gets its own RunPool instance
    (mine-adhoc, process-item, qa-check, ...). ``pool status`` only
    inspects one. Cloud workers add another axis: each Batch worker
    writes its own per-step pool dir on Filestore. To answer
    "is the pool actually rotating across the whole run?" — the local
    pool inspection commands need a roll-up. This is it.
    """
    out = get_output()

    sub_pools = _collect_sub_pools(run_dir, with_auth_outcomes=auth_outcomes)
    if not sub_pools:
        out.progress(f"No sub-step pool dirs found under {run_dir}")
        raise typer.Exit(code=0)

    rollup = _build_rollup(sub_pools, with_auth_outcomes=auth_outcomes)
    if as_json:
        out.data(json.dumps(rollup, indent=2))
        return
    _render_rollup(rollup, out=out, with_auth_outcomes=auth_outcomes)


def _collect_sub_pools(
    run_dir: Path,
    *,
    with_auth_outcomes: bool,
) -> list[dict[str, Any]]:
    """Walk *run_dir* and collect one entry per sub-step pool.

    Status files live at ``<run>/.state/steps/<step_id>/runpool-status.yaml``
    with matching events at ``<run>/.logs/runpool/steps/<step_id>/events.jsonl``.
    Composite parent runs (e.g. EIA dispatch) also discover pools nested under
    ``<parent>/<step_id>/.state/steps/...`` via :func:`_iter_composite_run_dirs`,
    so a rollup invoked on the parent answers "every sub-pool below this
    composite run" rather than only the parent's own steps.

    Each entry is a flat dict (not a dataclass) so the same shape flows
    through ``--json`` output and the text renderer. ``step_dir`` is the
    relative path from *run_dir* down to the per-step state dir; that lets
    the renderer disambiguate same-named steps that live in different
    composite children.
    """
    entries: list[dict[str, Any]] = []
    candidates: list[tuple[Path, Path, str]] = []
    seen_status_files: set[Path] = set()

    for sub_run in _iter_composite_run_dirs(run_dir):
        steps_state_root = paths_mod.run_state_dir(sub_run) / paths_mod.STEPS_SUBDIR
        if not steps_state_root.exists():
            continue
        try:
            sub_run_rel = sub_run.relative_to(run_dir)
        except ValueError:
            sub_run_rel = Path(sub_run.name)
        rel_prefix = "" if sub_run == run_dir else f"{sub_run_rel.as_posix()}/"
        for step_state in sorted(steps_state_root.iterdir()):
            if not step_state.is_dir():
                continue
            status_file = step_state / POOL_STATUS_FILE
            if not status_file.exists():
                continue
            if status_file in seen_status_files:
                continue
            seen_status_files.add(status_file)
            events_file = paths_mod.runpool_step_events_for_read(sub_run, step_state.name)
            candidates.append((status_file, events_file, f"{rel_prefix}{step_state.name}"))

    for status_file, events_file, rel in candidates:
        try:
            status = read_status(status_file)
        except Exception:  # noqa: BLE001 — surface parse failures inline rather than fail the whole walk
            entries.append(
                {
                    "step_dir": str(rel),
                    "status": None,
                    "auth_outcomes": [],
                    "error": f"failed to read {status_file}",
                }
            )
            continue
        auth_outcomes_for_step: list[dict[str, Any]] = []
        if with_auth_outcomes and events_file.is_file():
            for ev in iter_jsonl_objects(events_file):
                if ev.get("event") == "auth_outcome":
                    auth_outcomes_for_step.append(ev)
        entries.append(
            {
                "step_dir": str(rel),
                "status": status.model_dump(mode="json"),
                "auth_outcomes": auth_outcomes_for_step,
            }
        )
    return entries


def _build_rollup(
    sub_pools: list[dict[str, Any]],
    *,
    with_auth_outcomes: bool,
) -> dict[str, Any]:
    """Aggregate per-step + cross-step totals from a sub-pools list."""
    completed = 0
    failed = 0
    killed = 0
    active = 0
    pending_retries = 0
    per_step: list[dict[str, Any]] = []
    lane_totals: dict[str, dict[str, Any]] = {}
    for entry in sub_pools:
        status = entry.get("status")
        step_summary: dict[str, Any] = {
            "step_dir": entry["step_dir"],
        }
        if status is None:
            step_summary["error"] = entry.get("error") or "no status"
            per_step.append(step_summary)
            continue
        completed += int(status.get("completed_count", 0) or 0)
        failed += int(status.get("failed_count", 0) or 0)
        killed += int(status.get("killed_count", 0) or 0)
        active += int(status.get("active_count", 0) or 0)
        pending_retries += int(status.get("pending_retries", 0) or 0)
        step_summary.update(
            {
                "pool_id": status.get("pool_id"),
                "backend": status.get("backend"),
                "max_concurrency": status.get("max_concurrency"),
                "current_concurrency": status.get("current_concurrency"),
                "active_count": status.get("active_count"),
                "completed_count": status.get("completed_count"),
                "failed_count": status.get("failed_count"),
                "killed_count": status.get("killed_count"),
                "pending_retries": status.get("pending_retries"),
                "pressure_level": (status.get("pressure") or {}).get("level"),
            }
        )
        if status.get("concurrency_plan") is not None:
            step_summary["concurrency_plan"] = status.get("concurrency_plan")
        if with_auth_outcomes:
            step_summary["auth_outcome_count"] = len(entry.get("auth_outcomes", []))
        lanes_for_step: list[dict[str, Any]] = []
        for lane in status.get("lanes") or []:
            if not isinstance(lane, dict):
                continue
            lane_id = lane.get("lane_id")
            if not lane_id:
                continue
            lanes_for_step.append(lane)
            agg = lane_totals.setdefault(
                lane_id,
                {
                    "lane_id": lane_id,
                    "execution_profile": lane.get("execution_profile"),
                    "adapter": lane.get("adapter"),
                    "comparison_role": lane.get("comparison_role"),
                    "active": 0,
                    "completed": 0,
                    "failed": 0,
                    "killed": 0,
                },
            )
            agg["active"] += int(lane.get("active_count", 0) or 0)
            agg["completed"] += int(lane.get("completed_count", 0) or 0)
            agg["failed"] += int(lane.get("failed_count", 0) or 0)
            agg["killed"] += int(lane.get("killed_count", 0) or 0)
        if lanes_for_step:
            step_summary["lanes"] = lanes_for_step
        per_step.append(step_summary)

    rollup: dict[str, Any] = {
        "sub_pool_count": len(sub_pools),
        "totals": {
            "completed": completed,
            "failed": failed,
            "killed": killed,
            "active": active,
            "pending_retries": pending_retries,
        },
        "per_step": per_step,
    }
    if lane_totals:
        rollup["lanes"] = list(lane_totals.values())

    if with_auth_outcomes:
        labels: Counter[str] = Counter()
        classifications: Counter[str] = Counter()
        per_step_label: dict[str, Counter[str]] = {}
        retries: Counter[int] = Counter()
        rotated = 0
        total_auth = 0
        for entry in sub_pools:
            outcomes = entry.get("auth_outcomes") or []
            total_auth += len(outcomes)
            step_dir = entry["step_dir"]
            for ev in outcomes:
                label = ev.get("label", "")
                classification = ev.get("classification", "")
                labels[label] += 1
                classifications[classification] += 1
                retries[int(ev.get("retry_count", 0) or 0)] += 1
                if ev.get("rotated"):
                    rotated += 1
                per_step_label.setdefault(step_dir, Counter())[label] += 1
        rollup["auth_outcomes"] = {
            "total": total_auth,
            "by_label": dict(labels),
            "by_classification": dict(classifications),
            "by_step_label": {step: dict(c) for step, c in per_step_label.items()},
            "retry_distribution": dict(sorted(retries.items())),
            "rotated": rotated,
        }
    return rollup


def _render_rollup(rollup: dict[str, Any], *, out: Any, with_auth_outcomes: bool) -> None:
    """Render a rollup report as plain text."""
    n = rollup["sub_pool_count"]
    totals = rollup["totals"]
    out.data(f"Sub-pools: {n}")
    out.data(
        f"Totals:    completed={totals['completed']}  failed={totals['failed']}  "
        f"killed={totals['killed']}  active={totals['active']}  "
        f"pending_retries={totals['pending_retries']}"
    )
    out.data("\nPer step:")
    for step in rollup["per_step"]:
        if "error" in step:
            out.data(f"  {step['step_dir']:30s}  ERROR: {step['error']}")
            continue
        line = (
            f"  {step['step_dir']:30s}  "
            f"completed={step['completed_count']:4d}  "
            f"failed={step['failed_count']:3d}  "
            f"active={step['active_count']:3d}  "
            f"conc={step['current_concurrency']}/{step['max_concurrency']}"
        )
        if with_auth_outcomes:
            line += f"  auth_outcomes={step.get('auth_outcome_count', 0)}"
        out.data(line)
        plan_summary = _format_concurrency_plan_summary(step.get("concurrency_plan"))
        if plan_summary:
            out.data(f"    plan: {plan_summary}")

    lanes = rollup.get("lanes") or []
    if lanes:
        out.data("\nPer lane:")
        for lane in lanes:
            profile = lane.get("execution_profile") or "-"
            role = f" role={lane.get('comparison_role')}" if lane.get("comparison_role") else ""
            out.data(
                f"  {lane['lane_id']:30s}  profile={profile}{role}  "
                f"completed={lane['completed']:4d}  failed={lane['failed']:3d}  "
                f"killed={lane['killed']:3d}  active={lane['active']:3d}"
            )

    if not with_auth_outcomes:
        return

    auth = rollup.get("auth_outcomes") or {}
    out.data(f"\nAuth outcomes (cross-step): {auth.get('total', 0)} total")
    if auth.get("total", 0) == 0:
        return
    out.data("\n  By label:")
    for label, count in sorted(auth["by_label"].items(), key=lambda kv: -kv[1]):
        out.data(f"    {label or '(none)':12s}  {count}")
    out.data("\n  By classification:")
    for cls, count in sorted(auth["by_classification"].items(), key=lambda kv: -kv[1]):
        out.data(f"    {cls or '(none)':12s}  {count}")
    if auth.get("by_step_label"):
        out.data("\n  By step × label:")
        for step, by_label in auth["by_step_label"].items():
            out.data(f"    {step}: {by_label}")
    out.data(f"\n  Rotations: {auth.get('rotated', 0)}")


@pool_app.command("retry-missing")
def retry_missing(
    run_dir: Path = typer.Argument(..., help="Local run directory"),
    cloud_runs_dir: str = typer.Option(
        ..., "--cloud-runs-dir", help="Cloud storage path to check output existence"
    ),
    variant: str | None = typer.Option(None, "--variant", help="Filter to a specific variant"),  # noqa: UP007
    expected_files: str = typer.Option(
        "", "--expected-files", help="Comma-separated output filenames to check"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be reset without changing state"
    ),
) -> None:
    """Reset completion markers for items where cloud output is missing.

    Scans the local run directory for items marked "completed", checks if
    expected output files exist at the cloud path, and resets missing items
    to "failed" so run-parallel will retry them.
    """
    out = get_output()
    cloud_base = Path(cloud_runs_dir)

    if not run_dir.is_dir():
        out.data(f"Run directory not found: {run_dir}")
        raise typer.Exit(code=1)

    expected = [f.strip() for f in expected_files.split(",") if f.strip()] if expected_files else []

    # Discover per-step task state roots at <run>/.state/tasks/<step_id>/.
    # --variant filters by step_id.
    tasks_root = run_dir / STATE_DIR / "tasks"
    variant_dirs: list[Path] = []
    if tasks_root.exists():
        for candidate in sorted(tasks_root.iterdir()):
            if not candidate.is_dir():
                continue
            if variant and candidate.name != variant:
                continue
            has_items = any(
                (item / "status.yaml").exists() for item in candidate.iterdir() if item.is_dir()
            )
            if has_items:
                variant_dirs.append(candidate)

    if not variant_dirs:
        out.data("No variant directories found")
        raise typer.Exit(code=1)

    total_reset = 0
    total_checked = 0

    for vdir in variant_dirs:
        variant_name = vdir.name
        cloud_variant_dir = cloud_base / variant_name

        for item_d in sorted(vdir.iterdir()):
            if not item_d.is_dir():
                continue
            # item_d is the per-task state dir; status.yaml lives directly inside.
            record = read_status_at(item_d)
            if record is None or record.state != "completed":
                continue

            total_checked += 1
            item_name = item_d.name
            cloud_item_dir = cloud_variant_dir / item_name

            # Check output existence on cloud
            missing: list[str] = []
            if expected:
                for fname in expected:
                    if not (cloud_item_dir / fname).exists():
                        missing.append(fname)
            elif not cloud_item_dir.is_dir():
                missing.append("<directory>")
            else:
                # No expected files specified — check dir is non-empty (has files, not just subdirs)
                has_output = any(f.is_file() for f in cloud_item_dir.iterdir())
                if not has_output:
                    missing.append("<no output files>")

            if missing:
                total_reset += 1
                if dry_run:
                    out.data(
                        f"  would reset  {variant_name}/{item_name}: missing {', '.join(missing)}"
                    )
                else:
                    # Reset to failed so run-parallel retries it
                    reset_record = StatusRecord(
                        run_id=record.run_id,
                        step_id=record.step_id,
                        item=record.item,
                        state="failed",
                        attempt=record.attempt,
                        started_at=record.started_at,
                        error=f"cloud output missing: {', '.join(missing)}",
                    )
                    write_status_at(item_d, reset_record)
                    out.data(f"  reset  {variant_name}/{item_name}: missing {', '.join(missing)}")

    action = "Would reset" if dry_run else "Reset"
    out.data(f"\n{action}: {total_reset}/{total_checked} completed items missing cloud output")


@pool_app.command("override")
def pool_override(
    run_dir: Path = typer.Argument(..., help="Run directory (will walk all sub-step pools)"),
    cap: int | None = typer.Option(
        None,
        "--cap",
        help=(
            "New temporary operator_cap to apply across every active pool. "
            "Bounded by launch-time --max-concurrency; cannot raise effective "
            "concurrency above the launch envelope. To lift the envelope itself, "
            "use --max-concurrency."
        ),
    ),
    max_concurrency: int | None = typer.Option(
        None,
        "--max-concurrency",
        help=(
            "New temporary launch-envelope ceiling. Lifts the upper bound the "
            "adaptive memory + provider governors ramp toward. Use when the "
            "adaptive scaler has converged at the launch-time --max-concurrency "
            "and the host has memory + provider headroom to go higher. The "
            "controller will ramp memory_ceiling and provider_ceiling up to "
            "this new ceiling on subsequent pressure checks. See logbook "
            "arb-2026-05-28-thu-ensemble § L7 + bead internal issue."
        ),
    ),
    mode: str = typer.Option("adaptive", "--mode", help="Pool mode override: adaptive | manual"),
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Remove temporary overrides so pools return to normal adaptive ceilings",
    ),
) -> None:
    """Set or clear temporary operator overrides for live RunPools.

    A composite dispatch layout has one pool per agent step under
    ``<run-dir>/<child-process>/.state/steps/<step>/``. This command writes
    run-level overrides under every discovered ``.state`` root for future
    pools, then walks every dir containing a ``scale-state.yaml`` (= a pool
    that has initialized) and writes the override alongside it. Live pools
    read it on the next pressure check (~10s default) without restart;
    future pools honor the nearest run-level cap before launching their
    first item.

    Two override modes:

    - ``--cap`` adjusts operator_cap within the launch envelope. Use to
      temporarily *reduce* concurrency during a live incident, or to *raise*
      it back up after a controller-induced downscale (within launch
      max_concurrency). Cannot exceed launch-time --max-concurrency.

    - ``--max-concurrency`` lifts the launch envelope itself. Use when the
      adaptive scaler has converged at the launch-time cap and the host has
      headroom to go higher. The adaptive controller picks up the new ceiling
      and ramps memory + provider ceilings up to it over subsequent pressure
      checks.

    Use ``--clear`` after the intervention so future pools are not pinned by
    a stale override.
    """

    out = get_output()

    if clear and (cap is not None or max_concurrency is not None):
        raise typer.BadParameter("--clear cannot be combined with --cap or --max-concurrency")
    if not clear and cap is None and max_concurrency is None:
        raise typer.BadParameter(
            "Provide --cap and/or --max-concurrency to set an override, or --clear to remove"
        )
    if cap is not None and cap < 1:
        raise typer.BadParameter(f"--cap must be >= 1, got {cap}")
    if max_concurrency is not None and max_concurrency < 1:
        raise typer.BadParameter(f"--max-concurrency must be >= 1, got {max_concurrency}")
    if mode not in ("adaptive", "manual"):
        raise typer.BadParameter(f"--mode must be 'adaptive' or 'manual', got {mode!r}")

    state_files, override_paths = _pool_override_paths(run_dir, SCALE_OVERRIDE_FILE)

    if clear:
        removed = 0
        seen: set[Path] = set()
        for override_path in override_paths:
            if override_path in seen:
                continue
            seen.add(override_path)
            if override_path.exists():
                override_path.unlink()
                out.data(f"  removed override {override_path}")
                removed += 1
        out.data(
            f"\nRemoved {removed} override file(s). Active pools will return to normal "
            "adaptive ceilings at next pressure check (~10s); future pools will not "
            "inherit a stale operator override."
        )
        return

    override = ScaleOverride(mode=mode, operator_cap=cap, max_concurrency=max_concurrency)
    written = 0
    written_paths: set[Path] = set()
    desc_parts: list[str] = []
    if cap is not None:
        desc_parts.append(f"operator_cap={cap}")
    if max_concurrency is not None:
        desc_parts.append(f"max_concurrency={max_concurrency}")
    desc_parts.append(f"mode={mode}")
    desc = ", ".join(desc_parts)
    for override_path in override_paths:
        if override_path in written_paths:
            continue
        written_paths.add(override_path)
        write_scale_override(override_path, override)
        out.data(f"  wrote {desc} to {override_path}")
        written += 1
    if not state_files:
        out.progress(
            f"No active per-step pools found under {run_dir}; wrote run-level override for future pools."
        )
    out.data(
        f"\nWrote override to {written} location(s). Active pools will pick up at next "
        "pressure check (~10s); future pools inherit it at startup."
    )


def _pool_override_paths(
    run_dir: Path,
    scale_override_file: str,
) -> tuple[list[Path], list[Path]]:
    """Return active scale-state files and all candidate override paths."""
    override_paths = [paths_mod.run_state_dir(run_dir) / scale_override_file]
    state_files = list(run_dir.rglob("scale-state.yaml"))
    state_roots = [state_dir for state_dir in run_dir.rglob(STATE_DIR) if state_dir.is_dir()]
    override_paths.extend(state_root / scale_override_file for state_root in state_roots)
    for state_file in state_files:
        for candidate in (state_file.parent, *state_file.parents):
            if candidate.name == STATE_DIR:
                override_paths.append(candidate / scale_override_file)
                break
    override_paths.extend(state_file.parent / scale_override_file for state_file in state_files)
    return state_files, override_paths
