"""``metaproc trace`` — unified workflow event-trace CLI.

C1 ships ``--extract``: walk the run dir, run every detected extractor,
link cross-source parents + propagate status, write to
``<run-dir>/.logs/derived/trace.jsonl``. C2 adds filter + table + drill + tree
view modes on top of the stored trace. C3-C6 add rollup / health /
cost / quality.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from metaproc import paths as paths_mod
from metaproc.cli import app
from metaproc.plugins.discovery import get_plugin_registry
from metaproc.trace.aggregation import aggregate, format_rollup_table, supported_metrics
from metaproc.trace.named_views import (
    cost_rows,
    format_cost,
    format_health,
    format_source_tool,
    health_rows,
    source_tool_rows,
)
from metaproc.trace.quality import (
    VALIDATOR_NAMES,
    directive_compliance_rows,
    format_directive_compliance,
    format_runbook_completion,
    runbook_completion_rows,
)
from metaproc.trace.runner import extract_trace
from metaproc.trace.schema import TraceEvent
from metaproc.trace.store import read_trace, trace_path, write_trace
from metaproc.trace.views import (
    Filter,
    apply_filter,
    format_drill,
    format_projected_table,
    format_table,
    format_tree,
    project_spans,
)

# Named presets for the most common rollup queries.
# Each preset expands to (group_keys, metrics, optional_extra_filter).
TRACE_PRESETS: dict[str, dict[str, list[str]]] = {
    "by-adapter-step-tool": {
        "group": ["adapter.type", "step.id", "tool.family", "tool.name"],
        "metric": ["count", "duration_ms", "sum:tool.result.size_bytes"],
    },
    "cost-by-step": {
        "group": ["adapter.type", "step.id"],
        "metric": [
            "sum:attempt.cost_usd",
            "sum:attempt.tokens_input",
            "sum:attempt.tokens_output",
        ],
        "filter_kind": ["attempt"],
    },
}


@app.command()
def trace(
    run_dir: Path = typer.Argument(..., help="Path to the run directory"),
    extract: bool = typer.Option(
        False,
        "--extract",
        help="Run every detected extractor and write <run-dir>/.logs/derived/trace.jsonl.",
    ),
    trace_id: str | None = typer.Option(  # noqa: UP007
        None,
        "--trace-id",
        help="Override the trace_id (defaults to run-dir basename).",
    ),
    # Filter flags (C2)
    kind: str | None = typer.Option(None, "--kind", help="Filter to one span kind"),  # noqa: UP007
    source: str | None = typer.Option(None, "--source", help="Filter to one extractor source"),  # noqa: UP007
    item_key: str | None = typer.Option(  # noqa: UP007
        None,
        "--item",
        help="Filter to one item.key (the framework-canonical per-item identifier).",
    ),
    attr: list[str] | None = typer.Option(  # noqa: UP007
        None,
        "--attr",
        help=(
            "Filter by an arbitrary attribute, e.g. --attr item.category=sample. "
            "Repeatable; multiple --attr filters are AND-combined."
        ),
    ),
    step: str | None = typer.Option(None, "--step", help="Filter to one step.id"),  # noqa: UP007
    tool: str | None = typer.Option(None, "--tool", help="Filter to one tool.name"),  # noqa: UP007
    status: str | None = typer.Option(None, "--status", help="Filter to one span status"),  # noqa: UP007
    error_code: str | None = typer.Option(  # noqa: UP007
        None,
        "--error-code",
        help=(
            "Filter to spans whose error.code matches the given value (e.g. "
            "quota_exhausted, auth_required, agent_timeout). Populated by the "
            "claude-agent extractor."
        ),
    ),
    drill: str | None = typer.Option(  # noqa: UP007
        None, "--drill", help="Show full detail for one span (by span_id)."
    ),
    tree: bool = typer.Option(
        False, "--tree", help="Render filtered spans as a hierarchical tree."
    ),
    # Named views (C4, C5)
    health: bool = typer.Option(
        False,
        "--health",
        help="Failure-surface view: non-ok status clusters by source+status.",
    ),
    cost: bool = typer.Option(
        False,
        "--cost",
        help="Cost reconciliation across every trace source.",
    ),
    quality: str | None = typer.Option(  # noqa: UP007
        None,
        "--quality",
        help=f"Run a quality validator. Supported: {sorted(VALIDATOR_NAMES)}.",
    ),
    source_tool_rollup: bool = typer.Option(
        False,
        "--source-tool-rollup",
        help=(
            "Generic source/tool rollup grouped by item, step, source, profile, "
            "tool family/operation, origin, provider, status, cutoff, and raw-log coverage."
        ),
    ),
    # Rollup mode (C3)
    rollup: bool = typer.Option(False, "--rollup", help="Aggregate over filtered spans."),
    group: list[str] | None = typer.Option(  # noqa: UP007
        None,
        "--group",
        "--group-by",
        help="Group key(s) for --rollup. Repeatable or comma-separated.",
    ),
    metric: list[str] | None = typer.Option(  # noqa: UP007
        None,
        "--metric",
        help=(
            "Metric(s) for --rollup. Repeatable or comma-separated. "
            f"Named: {sorted(supported_metrics())}; "
            "or use sum:<attr> / avg:<attr> / max:<attr> / min:<attr>."
        ),
    ),
    preset: str | None = typer.Option(  # noqa: UP007
        None,
        "--preset",
        help=(
            f"Apply a named rollup preset (implies --rollup). Available: {sorted(TRACE_PRESETS)}."
        ),
    ),
    project: list[str] | None = typer.Option(  # noqa: UP007
        None,
        "--project",
        help=(
            "Projection-only mode: emit filtered rows with the given columns "
            "(repeatable or comma-separated). Skips aggregation; useful for "
            "listing every web search query, every tool input path, etc."
        ),
    ),
    # Output flags
    as_json: bool = typer.Option(False, "--json", help="Emit filtered spans as JSON array."),
    as_jsonl: bool = typer.Option(False, "--jsonl", help="Emit filtered spans as JSONL."),
    print_count: bool = typer.Option(False, "--count", help="Print by-kind tally to stderr."),
) -> None:
    """Build and query a unified ``TraceEvent/0.1`` trace for a run.

    Modes:
    ``--extract`` writes ``<run-dir>/.logs/derived/trace.jsonl``;
    no flag loads the existing trace; ``--kind`` / ``--source`` /
    ``--item`` / ``--step`` / ``--tool`` / ``--status`` filter the
    rendered output. ``--tree`` switches to hierarchical view;
    ``--drill <id>`` shows full detail for one span.
    """
    if not run_dir.is_dir():
        raise typer.BadParameter(f"run_dir not found: {run_dir}")

    spans: list[TraceEvent]
    if extract:
        spans = extract_trace(run_dir, trace_id=trace_id)
        out_path = write_trace(run_dir, spans)
        sys.stderr.write(f"wrote {len(spans)} spans to {out_path}\n")
    else:
        read_path = paths_mod.trace_out_for_read(run_dir)
        if not read_path.is_file():
            sys.stderr.write(
                f"no trace found at {trace_path(run_dir)}. Run with --extract first.\n"
            )
            raise typer.Exit(code=1)
        spans = read_trace(run_dir)

    if print_count:
        _print_counts(spans)

    if drill:
        found = next((s for s in spans if s.span_id == drill), None)
        if found is None:
            sys.stderr.write(f"span_id {drill} not found in trace\n")
            raise typer.Exit(code=1)
        typer.echo(format_drill(found), nl=False)
        return

    flt = Filter(
        kind=kind,
        source=source,
        item_key=item_key,
        step=step,
        tool=tool,
        status=status,
        error_code=error_code,
        attrs=_parse_attr_filters(attr),
    )
    filtered = apply_filter(spans, flt)

    if health:
        rows = health_rows(filtered)
        if as_json:
            typer.echo(json.dumps(rows, indent=2))
        else:
            typer.echo(format_health(rows), nl=False)
        return

    if cost:
        rows = cost_rows(filtered)
        if as_json:
            typer.echo(json.dumps(rows, indent=2))
        else:
            typer.echo(format_cost(rows), nl=False)
        return

    if quality:
        if quality not in VALIDATOR_NAMES:
            raise typer.BadParameter(
                f"unknown quality validator {quality!r}. Supported: {sorted(VALIDATOR_NAMES)}."
            )
        if quality == "runbook-completion":
            rows = runbook_completion_rows(filtered)
            if as_json:
                typer.echo(json.dumps(rows, indent=2))
            else:
                typer.echo(format_runbook_completion(rows), nl=False)
        else:
            rows = directive_compliance_rows(filtered, get_plugin_registry().quality_directives)
            if as_json:
                typer.echo(json.dumps(rows, indent=2))
            else:
                typer.echo(format_directive_compliance(rows), nl=False)
        return

    if source_tool_rollup:
        rows = source_tool_rows(filtered)
        if as_json:
            typer.echo(json.dumps(rows, indent=2))
        else:
            typer.echo(format_source_tool(rows), nl=False)
        return

    # P2.1 — --project: emit filtered rows with explicit columns,
    # skipping aggregation entirely. Useful for "list every web search query"
    # and similar slice-and-dice queries.
    if project:
        columns = _split_csv(project)
        if not columns:
            raise typer.BadParameter("--project needs at least one column.")
        rows_proj = project_spans(filtered, columns)
        if as_json:
            typer.echo(
                json.dumps([dict(zip(columns, r, strict=False)) for r in rows_proj], indent=2)
            )
        else:
            typer.echo(format_projected_table(rows_proj, columns), nl=False)
        return

    # P2.1 — --preset expands to known group/metric/filter combos.
    if preset:
        if preset not in TRACE_PRESETS:
            raise typer.BadParameter(
                f"unknown preset {preset!r}. Available: {sorted(TRACE_PRESETS)}."
            )
        cfg = TRACE_PRESETS[preset]
        group_keys = _split_csv(group) or list(cfg["group"])
        metrics = _split_csv(metric) or list(cfg["metric"])
        preset_spans = filtered
        filter_kind = cfg.get("filter_kind")
        if filter_kind:
            preset_spans = [s for s in preset_spans if s.kind in filter_kind]
        rows = aggregate(preset_spans, group_keys=group_keys, metrics=metrics)
        if as_json:
            typer.echo(json.dumps(rows, indent=2))
        else:
            typer.echo(format_rollup_table(rows, group_keys=group_keys, metrics=metrics), nl=False)
        return

    if rollup:
        group_keys = _split_csv(group) or ["kind"]
        metrics = _split_csv(metric) or ["count"]
        rows = aggregate(filtered, group_keys=group_keys, metrics=metrics)
        if as_json:
            typer.echo(json.dumps(rows, indent=2))
        else:
            typer.echo(format_rollup_table(rows, group_keys=group_keys, metrics=metrics), nl=False)
        return

    if tree:
        typer.echo(format_tree(filtered), nl=False)
        return

    if as_json:
        typer.echo(json.dumps([s.model_dump(mode="json") for s in filtered], indent=2))
        return

    if as_jsonl:
        for s in filtered:
            typer.echo(s.model_dump_json())
        return

    # Default: table view (or noop when extract was the only intent).
    if not extract or kind or source or item_key or step or tool or status or error_code or attr:
        typer.echo(format_table(filtered), nl=False)


def _parse_attr_filters(values: list[str] | None) -> tuple[tuple[str, str], ...]:
    """Parse ``--attr key=value`` flags into ``Filter.attrs`` tuples.

    Both repeated flags and comma-separated lists are accepted:
    ``--attr item.category=sample --attr provider=example`` is equivalent to
    ``--attr item.category=sample,provider=example``.
    """
    if not values:
        return ()
    out: list[tuple[str, str]] = []
    for raw in values:
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            if "=" not in piece:
                raise typer.BadParameter(f"--attr value {piece!r} must be in key=value form.")
            key, _, value = piece.partition("=")
            out.append((key.strip(), value.strip()))
    return tuple(out)


def _split_csv(values: list[str] | None) -> list[str]:
    """Allow repeatable + comma-separated flags: ``--group step.id,kind`` or
    ``--group step.id --group kind`` both produce ``["step.id", "kind"]``.
    """
    if not values:
        return []
    out: list[str] = []
    for v in values:
        out.extend(p.strip() for p in v.split(",") if p.strip())
    return out


def _print_counts(spans: list[TraceEvent]) -> None:
    """Render a brief by-kind tally to stderr."""
    by_kind: dict[str, int] = {}
    for s in spans:
        by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
    sys.stderr.write("spans by kind:\n")
    for kind_name in sorted(by_kind):
        sys.stderr.write(f"  {kind_name:<16} {by_kind[kind_name]}\n")
