"""metaproc compare-trace — cross-run-dir trace-store comparison matrix.

P4 (internal issue) under spec
`docs/project/specs/active/plan-2026-05-26-multi-adapter-resource-rollup.md`.

Reads `<run-dir>/.logs/derived/trace.jsonl` from N run-dirs and produces
a row-per-(group) × column-per-run-dir matrix using the same
``aggregate()`` primitive as ``metaproc trace --rollup``. Defaults to
grouping by ``adapter.type,step.id,tool.family`` and counting tool_call
spans, which is the canonical "what did each lane do at each step?"
view across a multi-lane batch.

Sibling to ``metaproc compare-matrix`` (which compares frontmatter
across variants of a single process_dir). The two have different
storage contracts so they're separate commands rather than two modes
of one command.

Run with::

    metaproc compare-trace <run-dir-1> <run-dir-2> ...
    metaproc compare-trace <r1> <r2> --group-by adapter.type,tool.family --metric count
    metaproc compare-trace <r1> <r2> --metric "sum:attempt.cost_usd" \\
        --group-by adapter.type,step.id --filter-kind attempt
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from metaproc.cli import app
from metaproc.errors import CLIError
from metaproc.trace.aggregation import aggregate, format_rollup_table
from metaproc.trace.schema import TraceEvent
from metaproc.trace.store import read_trace

DEFAULT_GROUP = ["adapter.type", "step.id", "tool.family"]
DEFAULT_METRICS = ["count"]


@app.command("compare-trace")
def compare_trace(
    run_dirs: list[Path] = typer.Argument(  # noqa: UP007
        ..., help="One or more run-dirs (each must contain .logs/derived/trace.jsonl)."
    ),
    group_by: str | None = typer.Option(  # noqa: UP007
        None,
        "--group-by",
        help=("Comma-separated group keys. Default: `adapter.type,step.id,tool.family`."),
    ),
    metric: list[str] | None = typer.Option(  # noqa: UP007
        None,
        "--metric",
        help=(
            "Repeatable. Named (`count`, `cost`, ...) or generic "
            "(`sum:<attr>`, `avg:<attr>`). Default: `count`."
        ),
    ),
    filter_kind: str | None = typer.Option(  # noqa: UP007
        None,
        "--filter-kind",
        help=(
            "Restrict to spans with `kind=<value>` before aggregating. "
            "Common: `tool_call` (default for count) or `attempt` (for cost roll-ups)."
        ),
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit matrix as JSON."),
) -> None:
    """Cross-run-dir trace comparison matrix.

    Loads the trace store from each run-dir, aggregates per the
    requested group keys + metrics, and emits a side-by-side matrix
    where rows are unique groupings and columns are run-dirs.
    """
    if len(run_dirs) < 2:
        raise CLIError("compare-trace needs at least 2 run-dirs")

    group_keys = _parse_group_keys(group_by) or list(DEFAULT_GROUP)
    metrics = list(metric) if metric else list(DEFAULT_METRICS)
    kind_filter = filter_kind or _default_kind_for_metrics(metrics)

    # Load + aggregate per run-dir.
    per_run: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}
    for run_dir in run_dirs:
        trace_path = run_dir / ".logs" / "derived" / "trace.jsonl"
        if not trace_path.is_file():
            raise CLIError(
                f"no trace store at {trace_path}; "
                "run `metaproc trace --extract` against this run-dir first."
            )
        spans = list(read_trace(run_dir))
        if kind_filter:
            spans = [s for s in spans if s.kind == kind_filter]
        rows = aggregate(spans, group_keys=group_keys, metrics=metrics)
        per_run[run_dir.name] = {tuple(str(r.get(k, "")) for k in group_keys): r for r in rows}

    # Build matrix: rows = union of group tuples across runs.
    all_groups: set[tuple[str, ...]] = set()
    for run_rows in per_run.values():
        all_groups.update(run_rows.keys())
    sorted_groups = sorted(all_groups)
    run_names = [r.name for r in run_dirs]

    if as_json:
        payload: list[dict[str, Any]] = []
        for grp in sorted_groups:
            row: dict[str, Any] = dict(zip(group_keys, grp, strict=False))
            for run_name in run_names:
                run_row = per_run[run_name].get(grp)
                for m in metrics:
                    cell_key = f"{run_name}/{m}" if len(metrics) > 1 else run_name
                    row[cell_key] = run_row.get(m) if run_row else None
            payload.append(row)
        typer.echo(json.dumps(payload, indent=2))
        return

    # Format the matrix flat: every (run, metric) is one column.
    column_keys: list[tuple[str, str]] = [(run_name, m) for run_name in run_names for m in metrics]
    header = list(group_keys) + [
        f"{run_name}/{m}" if len(metrics) > 1 else run_name for run_name, m in column_keys
    ]
    body: list[list[str]] = [header]
    for grp in sorted_groups:
        cells: list[str] = list(grp)
        for run_name, m in column_keys:
            run_row = per_run[run_name].get(grp)
            cells.append(_fmt(run_row.get(m) if run_row else None))
        body.append(cells)
    widths = [max(len(r[i]) for r in body) for i in range(len(header))]
    lines: list[str] = []
    for body_row in body:
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(body_row)))
    typer.echo("\n".join(lines))


def _parse_group_keys(group_by: str | None) -> list[str]:
    if not group_by:
        return []
    return [p.strip() for p in group_by.split(",") if p.strip()]


def _default_kind_for_metrics(metrics: list[str]) -> str | None:
    """Pick a sensible default kind filter from the requested metrics.

    `sum:attempt.*` metrics only live on attempt spans; otherwise default
    to tool_call (the most common rollup target).
    """
    if any(
        m.startswith(("sum:attempt.", "avg:attempt.", "max:attempt.", "min:attempt."))
        for m in metrics
    ):
        return "attempt"
    return "tool_call"


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        as_int = int(value)
        return str(as_int) if value == float(as_int) else f"{value:.4f}"
    return str(value)


# Helper exposed for tests + the validate_trace_store report.
def cross_run_matrix(
    run_dirs: list[Path],
    *,
    group_keys: list[str],
    metrics: list[str],
    kind_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Library-friendly version of the CLI body. Returns matrix rows.

    Each row is a dict ``{**group_key_values, "<run>/<metric>": <value>}``.
    Missing cells (group exists in one run but not another) come back as
    ``None``. Used by tests and by future report generators.
    """
    per_run: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}
    for run_dir in run_dirs:
        trace_path = run_dir / ".logs" / "derived" / "trace.jsonl"
        if not trace_path.is_file():
            msg = f"no trace store at {trace_path}"
            raise FileNotFoundError(msg)
        spans: list[TraceEvent] = list(read_trace(run_dir))
        if kind_filter:
            spans = [s for s in spans if s.kind == kind_filter]
        rows = aggregate(spans, group_keys=group_keys, metrics=metrics)
        per_run[run_dir.name] = {tuple(str(r.get(k, "")) for k in group_keys): r for r in rows}
    all_groups: set[tuple[str, ...]] = set()
    for run_rows in per_run.values():
        all_groups.update(run_rows.keys())
    run_names = [r.name for r in run_dirs]
    out: list[dict[str, Any]] = []
    for grp in sorted(all_groups):
        row: dict[str, Any] = dict(zip(group_keys, grp, strict=False))
        for run_name in run_names:
            run_row = per_run[run_name].get(grp)
            for m in metrics:
                cell_key = f"{run_name}/{m}" if len(metrics) > 1 else run_name
                row[cell_key] = run_row.get(m) if run_row else None
        out.append(row)
    return out


# Avoid clean unused-import warning while format_rollup_table stays
# available for callers that import this module directly.
_ = format_rollup_table
