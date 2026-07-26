"""``metaproc tools`` — adapter-aware tool-call query CLI.

Thin wrappers around the trace filter/project/aggregate surface with
tool-centric defaults. Two subcommands:

- ``metaproc tools list <run-dir>`` -- filtered row listing
- ``metaproc tools rollup <run-dir>`` -- aggregate by adapter+step+tool
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from metaproc import paths as paths_mod
from metaproc.cli import app
from metaproc.trace.aggregation import aggregate, format_rollup_table
from metaproc.trace.schema import TraceEvent
from metaproc.trace.store import read_trace
from metaproc.trace.views import (
    Filter,
    apply_filter,
    format_projected_table,
    project_spans,
)

tools_app = typer.Typer(
    name="tools",
    help="Query tool-call spans from the unified trace.",
    no_args_is_help=True,
)
app.add_typer(tools_app, name="tools")

_DEFAULT_LIST_COLUMNS = [
    "adapter.type",
    "step.id",
    "item.key",
    "tool.name",
    "tool.family",
    "status",
]

_DEFAULT_ROLLUP_GROUP = ["adapter.type", "step.id", "tool.name"]
_DEFAULT_ROLLUP_METRIC = ["count"]


def _load_trace(run_dir: Path) -> list[TraceEvent]:
    """Load the trace store, erroring clearly if absent."""
    read_path = paths_mod.trace_out_for_read(run_dir)
    if not read_path.is_file():
        sys.stderr.write(f"no trace found for {run_dir}. Run `metaproc trace --extract` first.\n")
        raise typer.Exit(code=1)
    return read_trace(run_dir)


def _build_filter(
    *,
    adapter: str | None,
    step: str | None,
    tool_family: str | None,
    tool: str | None,
) -> Filter:
    """Build a Filter with tool-centric attr constraints."""
    attrs: list[tuple[str, str]] = []
    if adapter:
        attrs.append(("adapter.type", adapter))
    if tool_family:
        attrs.append(("tool.family", tool_family))
    return Filter(
        kind="tool_call",
        step=step,
        tool=tool,
        attrs=tuple(attrs),
    )


def _split_csv(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for v in values:
        out.extend(p.strip() for p in v.split(",") if p.strip())
    return out


@tools_app.command("list")
def tools_list(
    run_dir: Path = typer.Argument(..., help="Path to the run directory"),
    adapter: str | None = typer.Option(None, "--adapter", help="Filter to one adapter.type"),  # noqa: UP007
    step: str | None = typer.Option(None, "--step", help="Filter to one step.id"),  # noqa: UP007
    tool_family: str | None = typer.Option(  # noqa: UP007
        None, "--tool-family", help="Filter to one tool.family (web, file, shell, ...)"
    ),
    tool: str | None = typer.Option(None, "--tool", help="Filter to one tool.name"),  # noqa: UP007
    project: list[str] | None = typer.Option(  # noqa: UP007
        None, "--project", help="Override default columns (repeatable or comma-separated)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit rows as JSON array"),
) -> None:
    """List tool-call spans with adapter-aware defaults."""
    if not run_dir.is_dir():
        raise typer.BadParameter(f"run_dir not found: {run_dir}")

    spans = _load_trace(run_dir)
    flt = _build_filter(adapter=adapter, step=step, tool_family=tool_family, tool=tool)
    filtered = apply_filter(spans, flt)

    columns = _split_csv(project) or list(_DEFAULT_LIST_COLUMNS)
    rows = project_spans(filtered, columns)

    if as_json:
        typer.echo(json.dumps([dict(zip(columns, r, strict=False)) for r in rows], indent=2))
    else:
        typer.echo(format_projected_table(rows, columns), nl=False)


@tools_app.command("rollup")
def tools_rollup(
    run_dir: Path = typer.Argument(..., help="Path to the run directory"),
    adapter: str | None = typer.Option(None, "--adapter", help="Filter to one adapter.type"),  # noqa: UP007
    step: str | None = typer.Option(None, "--step", help="Filter to one step.id"),  # noqa: UP007
    tool_family: str | None = typer.Option(  # noqa: UP007
        None, "--tool-family", help="Filter to one tool.family (web, file, shell, ...)"
    ),
    tool: str | None = typer.Option(None, "--tool", help="Filter to one tool.name"),  # noqa: UP007
    group_by: list[str] | None = typer.Option(  # noqa: UP007
        None, "--group-by", help="Group keys (default: adapter.type,step.id,tool.name)"
    ),
    metric: list[str] | None = typer.Option(  # noqa: UP007
        None, "--metric", help="Metrics (default: count)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit rows as JSON array"),
) -> None:
    """Aggregate tool-call spans by adapter, step, and tool."""
    if not run_dir.is_dir():
        raise typer.BadParameter(f"run_dir not found: {run_dir}")

    spans = _load_trace(run_dir)
    flt = _build_filter(adapter=adapter, step=step, tool_family=tool_family, tool=tool)
    filtered = apply_filter(spans, flt)

    group_keys = _split_csv(group_by) or list(_DEFAULT_ROLLUP_GROUP)
    metrics = _split_csv(metric) or list(_DEFAULT_ROLLUP_METRIC)

    rows = aggregate(filtered, group_keys=group_keys, metrics=metrics)

    if as_json:
        typer.echo(json.dumps(rows, indent=2))
    else:
        typer.echo(format_rollup_table(rows, group_keys=group_keys, metrics=metrics), nl=False)
