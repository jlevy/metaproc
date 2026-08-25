"""metaproc status — run progress snapshot."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer
from prettyfmt import fmt_timedelta
from ruamel.yaml import YAMLError

from metaproc.cli import app, get_output
from metaproc.engine.resource_finalization import (
    finalize_run_resources,
    infer_recovery_outcome,
    resource_artifacts_need_recovery,
)
from metaproc.engine.run_status import (
    # fmt: skip -- re-export block
    RunStatus,
    StepStatusEntry,
    check_completion,
    scan_run_status,
)
from metaproc.errors import CLIError
from metaproc.io import read_yaml_file
from metaproc.io.overrides import read_overrides
from metaproc.models.plan import Plan
from metaproc.models.runtime import StepState
from metaproc.output import OutputFormat
from metaproc.viz_loader import load_plan_bundle_from_run

log = logging.getLogger(__name__)


def _load_plan_from_run(run_dir: Path) -> Plan | None:
    """Best-effort: load the resolved plan recorded for this run.

    Reads ``<run>/.state/run-config.yaml`` for the process spec path and
    captured variables, then rebuilds the plan. Returns None on any failure
    so ``metaproc status`` keeps working even when the spec has moved,
    when the layout is too old, or when the plan would not build under
    current params.
    """
    config_path = run_dir / ".state" / "run-config.yaml"
    if not config_path.exists():
        return None
    try:
        config = read_yaml_file(config_path)
    except YAMLError as exc:
        log.debug("could not parse run-config.yaml under %s: %s", run_dir, exc)
        return None
    if not isinstance(config, dict):
        return None
    process_spec = config.get("process_spec")
    if not isinstance(process_spec, str) or not process_spec:
        return None
    variables_raw = config.get("variables")
    variables: dict[str, str] = {}
    if isinstance(variables_raw, dict):
        for key, value in variables_raw.items():
            if isinstance(key, str) and isinstance(value, str):
                variables[key] = value

    try:
        from metaproc.commands.helpers import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            load_process_spec,
        )
        from metaproc.engine.build_plan import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            build_plan,
        )
        from metaproc.engine.process_scope import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            expand_process_vars,
        )

        spec_path = Path(process_spec)
        spec = load_process_spec(spec_path)
        params = expand_process_vars(spec, variables, process_dir=spec_path.parent)
        return build_plan(
            spec,
            params,
            process_path=spec_path,
            adapter_override=_optional_config_string(config, "execution_profile"),
            artifact_namespace=_optional_config_string(config, "artifact_namespace"),
            validate_required_inputs=False,
            validate_spec=False,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info(
            "could not rebuild plan for run %s (%s); steps section will be omitted",
            run_dir,
            exc,
        )
        return None


def _optional_config_string(config: dict[object, object], key: str) -> str | None:
    """Return one non-empty immutable run-config identity field."""
    value = config.get(key)
    return value if isinstance(value, str) and value else None


def _recover_resource_artifacts(run_dir: Path, status: RunStatus) -> None:
    """Best-effort local recovery for an inactive run with resource evidence."""
    if status.is_active:
        return
    evidence_exists = (run_dir / ".state" / "run-config.yaml").exists() or (
        run_dir / ".logs" / "resource-events.jsonl"
    ).exists()
    if not evidence_exists:
        return
    try:
        needs_recovery = resource_artifacts_need_recovery(run_dir)
    except Exception:  # noqa: BLE001 - status must survive observability scan failures
        log.exception("could not inspect resource artifact freshness for %s", run_dir)
        return
    if not needs_recovery:
        return

    outcome = infer_recovery_outcome(run_dir, totals=status.totals)
    try:
        finalize_run_resources(
            run_dir,
            outcome=outcome,
            trigger="status",
            bundle=load_plan_bundle_from_run(run_dir),
        )
    except Exception:  # noqa: BLE001 - status remains available if reporting recovery fails
        log.exception("resource artifact recovery failed for inactive run %s", run_dir)


def _format_text(status: RunStatus, *, steps_only: bool = False, stale_only: bool = False) -> str:
    """Format RunStatus as a human-readable text summary.

    Renders a one-line ``Process:`` readiness summary plus a Steps table
    when the plan is available. Pass ``steps_only=True`` to suppress the
    variant counters and ``stale_only=True`` to filter the Steps table to
    non-current rows.
    """
    if steps_only:
        return _format_steps_section(status, stale_only=stale_only) or "Steps: (no plan)"

    lines: list[str] = []

    lines.append(f"Run: {status.run_dir.name}")
    if status.started_at:
        elapsed_str = fmt_timedelta(status.elapsed.total_seconds()) if status.elapsed else ""
        lines.append(f"Started: {status.started_at:%Y-%m-%d %H:%M:%S} ({elapsed_str} ago)")
    # Three-way label so the operator can distinguish work-in-flight from
    # quiescent-between-steps from truly terminal. `is_active` collapses
    # them; the sub-flags expose what's actually happening. This prevents the
    # variant table from reading 100% while the orchestrator has queued work.
    if status.items_running:
        status_label = "RUNNING"
    elif status.orchestrator_alive or (status.pending_retries > 0 and status.is_active):
        status_label = "WAITING"
    elif status.is_active:
        # Legacy backstop: is_active is True but neither sub-flag fired
        # (e.g. RunStatus came from a reader without the sub-flags).
        status_label = "RUNNING"
    else:
        status_label = "COMPLETE"
    if status.pending_retries > 0:
        status_label += f" ({status.pending_retries} retries pending)"
    lines.append(f"Status: {status_label}")
    if status.process_state is not None:
        non_current = sum(
            1 for entry in status.steps if entry.state in (StepState.stale, StepState.invalidated)
        )
        if status.process_state == "stale":
            verb = "need" if non_current != 1 else "needs"
            plural = "s" if non_current != 1 else ""
            lines.append(f"Process: stale ({non_current} step{plural} {verb} rerun)")
        else:
            lines.append("Process: current")
    lines.append("")

    # Variant table
    header = (
        f"  {'Variant':<35} {'Done':>5} {'Run':>4} {'Fail':>5} {'Pend':>5} {'Total':>6}   {'%':>4}"
    )
    sep = "  " + "\u2500" * 72
    lines.append(header)
    lines.append(sep)

    for v in status.variants:
        c = v.counts
        done = c.completed + c.cached
        pct = f"{done * 100 // c.total}%" if c.total > 0 else "0%"
        lines.append(
            f"  {v.variant:<35} {done:>5} {c.running:>4} {c.failed:>5} {c.pending:>5} {c.total:>6}   {pct:>4}"
        )

    if len(status.variants) > 1:
        t = status.totals
        done = t.completed + t.cached
        pct = f"{done * 100 // t.total}%" if t.total > 0 else "0%"
        lines.append(sep)
        lines.append(
            f"  {'Total':<35} {done:>5} {t.running:>4} {t.failed:>5} {t.pending:>5} {t.total:>6}   {pct:>4}"
        )

    # Retrying items
    for v in status.variants:
        for ri in v.retrying_items:
            lines.append(f"\nRetrying: {ri.item} (attempt {ri.attempt})")

    # Timing per variant
    for v in status.variants:
        if v.timing:
            t = v.timing
            eta_str = fmt_timedelta(t.eta_seconds) if t.eta_seconds else "?"
            lines.append(f"\nTiming ({v.variant}):")
            lines.append(
                f"  Avg: {fmt_timedelta(t.avg_seconds)} | "
                f"Min: {fmt_timedelta(t.min_seconds)} | "
                f"Max: {fmt_timedelta(t.max_seconds)} | "
                f"ETA: ~{eta_str}"
            )

    # System metrics
    if status.system:
        s = status.system
        lines.append("\nSystem:")
        lines.append(
            f"  Memory: {s.memory_available_pct:.0f}% free ({s.pressure_level}) | "
            f"Swap: {s.swap_used_gb:.1f}G"
        )
        if s.subprocess_count > 0:
            rss_gb = s.rss_bytes / (1024**3)
            lines.append(f"  Procs: {s.subprocess_count} agent subprocesses | RSS: {rss_gb:.1f} GB")

    overrides_doc = read_overrides(status.run_dir)
    if overrides_doc and overrides_doc.entries:
        lines.append(f"\nOverrides ({len(overrides_doc.entries)}):")
        for entry in sorted(overrides_doc.entries, key=lambda e: (e.step, e.at)):
            scope = entry.item or "step-wide"
            note = entry.note or ""
            if len(note) > 80:
                note = note[:79] + "…"
            lines.append(
                f"  {entry.step}  {entry.action}  ({scope})  by {entry.by}  @ {entry.at}"
                + (f'  "{note}"' if note else "")
            )

    # Auth Pool — per-label invocation distribution + warnings.
    # Spec: plan-2026-05-03-auth-observability-and-load-balancing.md
    # § metaproc status "Auth Pool" section. Best-effort: any failure
    # in the aggregator (missing pool, malformed events) downgrades to
    # silent skip so a flaky pool backend never breaks `metaproc
    # status` for the operator.
    auth_lines = _format_auth_pool_section(status.run_dir)
    if auth_lines:
        lines.append("")
        lines.extend(auth_lines)

    # Steps section — render by default once we have a plan, since the
    # Process: summary above promises a per-step breakdown.
    steps_block = _format_steps_section(status, stale_only=stale_only)
    if steps_block:
        lines.append("")
        lines.append(steps_block)

    return "\n".join(lines)


_STATE_LABEL_WIDTH = 11  # widest StepState string is "invalidated"


def _format_steps_section(status: RunStatus, *, stale_only: bool = False) -> str:
    """Render the per-step Steps table.

    Returns an empty string when ``status.steps`` is empty (no plan was
    available). When ``stale_only`` is set, rows in state ``current`` or
    ``missing`` are filtered out so the table only shows what needs
    attention.
    """
    if not status.steps:
        return ""
    entries: list[StepStatusEntry]
    if stale_only:
        entries = [e for e in status.steps if e.state not in (StepState.current, StepState.missing)]
        if not entries:
            return "Steps: all current (use --steps to see the full table)"
    else:
        entries = list(status.steps)

    id_width = max(len("step_id"), max(len(e.step_id) for e in entries))
    lines: list[str] = [
        "Steps:",
        (
            f"  {'step_id':<{id_width}}  {'state':<{_STATE_LABEL_WIDTH}}  "
            f"{'recorded':>16} → {'current':>16}  items / reason"
        ),
        "  " + "─" * (id_width + _STATE_LABEL_WIDTH + 16 + 16 + 20),
    ]
    for entry in entries:
        recorded = entry.recorded_hash or "----"
        current = entry.current_hash or "----"
        annotations: list[str] = []
        if entry.item_counts is not None:
            annotations.append(
                f"{entry.item_counts.get('completed', 0)}/{entry.item_counts.get('total', 0)} items"
            )
        if entry.reason:
            annotations.append(entry.reason)
        suffix = "  " + "; ".join(annotations) if annotations else ""
        lines.append(
            f"  {entry.step_id:<{id_width}}  {entry.state.value:<{_STATE_LABEL_WIDTH}}  "
            f"{recorded:>16} → {current:>16}{suffix}"
        )
    return "\n".join(lines)


def _format_auth_pool_section(run_dir: Path) -> list[str]:
    """Render the Auth Pool section if any auth events live under *run_dir*.

    Returns ``[]`` (collapsed in the caller) when:
    - the aggregator finds no events / no pool labels for this run; OR
    - any unexpected exception bubbles up — observability must never
      break `metaproc status`.
    """
    try:
        from metaproc.dispatch.auth_usage import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            aggregate_label_usage_for_run,
            detect_inconsistencies,
        )

        usage = aggregate_label_usage_for_run(run_dir)
    except Exception:  # noqa: BLE001 — best-effort observability
        return []
    if not usage:
        return []
    out: list[str] = ["Auth Pool:"]
    total = sum(u.invocations_total for u in usage.values()) or 1
    for (_adapter, label), u in sorted(usage.items()):
        pct = 100 * u.invocations_total / total
        ok = u.invocations_ok
        failed = u.invocations_failed
        out.append(
            f"  {label:10s}  {u.invocations_total:>5} inv  ({pct:>4.0f}%)  "
            f"{ok} ok / {failed} failed"
        )
    warnings: list[str] = []
    for (_a, label), u in sorted(usage.items()):
        for issue in detect_inconsistencies(u):
            warnings.append(f"  ⚠ {label}: {issue}")
    if warnings:
        out.append("  WARNINGS:")
        out.extend(warnings)
    return out


def _format_json(status: RunStatus) -> dict[str, object]:
    """Serialize RunStatus to a JSON-compatible dict."""
    return status.model_dump(mode="json")  # pyright: ignore[reportReturnType]


_STATUS_FORMAT_HUMAN = {"human", "text"}
_STATUS_FORMAT_JSON = {"json"}


@app.command()
def status(
    run_dir: str = typer.Argument(
        ...,
        help="Path to a locally visible run directory",
    ),
    variant: str | None = typer.Option(None, "--variant", help="Filter to a specific variant"),  # noqa: UP007
    check: str | None = typer.Option(
        None, "--check", help="Check mode: 'completed' or 'no-failures'"
    ),  # noqa: UP007
    no_system: bool = typer.Option(False, "--no-system", help="Skip system resource metrics"),
    format: str | None = typer.Option(  # noqa: UP007
        None, "--format", help="Output format: human (default) or json"
    ),
    failed: bool = typer.Option(False, "--failed", help="Show only failed items"),
    slow: int | None = typer.Option(None, "--slow", help="Show slowest N items"),  # noqa: UP007
    steps_only: bool = typer.Option(
        False,
        "--steps",
        help="Show only the Steps table (skips variants, timing, system, auth pool).",
    ),
    stale_only: bool = typer.Option(
        False,
        "--stale-only",
        help="Filter the Steps table to non-current rows (stale / invalidated / in_flight).",
    ),
) -> None:
    """Show progress and status for a locally visible run directory."""
    out = get_output()

    if format is not None and format not in _STATUS_FORMAT_HUMAN | _STATUS_FORMAT_JSON:
        raise CLIError(f"--format must be one of: human, json (got {format!r})")

    run_path = Path(run_dir)
    if not run_path.is_dir():
        raise CLIError(
            f"status: locally visible run directory not found: {run_path}. "
            "For a cloud run, use `metaproc gcp status <run-id>` or hydrate the run tree first."
        )
    plan = _load_plan_from_run(run_path)
    run_status = scan_run_status(run_path, variant=variant, include_system=not no_system, plan=plan)
    _recover_resource_artifacts(run_path, run_status)

    # Determine output format (CLI --format overrides global)
    use_json = (format in _STATUS_FORMAT_JSON) or (out.format == OutputFormat.JSON)

    # Check mode
    if check:
        result = check_completion(run_status, check)
        if use_json:
            json.dump(
                {
                    "passed": result.passed,
                    "exit_code": result.exit_code,
                    "reason": result.reason,
                    **_format_json(run_status),
                },
                sys.stdout,
                default=str,
            )
            sys.stdout.write("\n")
        raise SystemExit(result.exit_code)

    # Normal output
    if use_json:
        data = _format_json(run_status)
        if stale_only:
            raw_steps = data.get("steps")
            if isinstance(raw_steps, list):
                data["steps"] = [
                    entry
                    for entry in raw_steps
                    if isinstance(entry, dict) and entry.get("state") not in ("current", "missing")
                ]
        if steps_only:
            # Match the text formatter's --steps semantics: project the
            # payload down to the step-state surface only, so callers
            # scripting `metaproc status <run> --steps --format json`
            # don't have to pick through variant/timing/system noise.
            data = {
                "run_dir": data.get("run_dir"),
                "process_state": data.get("process_state"),
                "steps": data.get("steps", []),
            }
        json.dump(data, sys.stdout, default=str, indent=2)
        sys.stdout.write("\n")
    else:
        print(_format_text(run_status, steps_only=steps_only, stale_only=stale_only))
