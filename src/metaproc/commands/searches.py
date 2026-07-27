"""``metaproc searches`` — web-search-family trace query CLI.

Pre-filtered to ``tool.family=web`` spans with search-centric default
columns. One subcommand:

- ``metaproc searches list <run-dir>`` -- filtered row listing
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from metaproc import paths as paths_mod
from metaproc.cli import app
from metaproc.trace.schema import TraceEvent
from metaproc.trace.store import read_trace
from metaproc.trace.views import (
    Filter,
    apply_filter,
    format_projected_table,
    project_spans,
)

searches_app = typer.Typer(
    name="searches",
    help="Query web-search tool calls from the unified trace.",
    no_args_is_help=True,
)
app.add_typer(searches_app, name="searches")

_DEFAULT_LIST_COLUMNS = [
    "kind",
    "source",
    "adapter.type",
    "step.id",
    "item.key",
    "provider",
    "tool.name",
    "tool.input.query",
    "tool.input.url",
    "status",
]


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
    engine: str | None,
) -> Filter:
    """Build a Filter pre-constrained to web-family tool calls."""
    attrs: list[tuple[str, str]] = [("tool.family", "web")]
    if adapter:
        attrs.append(("adapter.type", adapter))
    return Filter(
        kind=None,
        step=step,
        tool=engine,
        attrs=tuple(attrs),
    )


def _is_search_like(span: TraceEvent) -> bool:
    """Return true for web-search spans, excluding web fetch/materialization rows."""
    operation = span.attributes.get("tool.operation")
    if operation:
        return str(operation) == "search"
    return bool(span.attributes.get("tool.input.query"))


def _split_csv(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for v in values:
        out.extend(p.strip() for p in v.split(",") if p.strip())
    return out


@searches_app.command("list")
def searches_list(
    run_dir: Path = typer.Argument(..., help="Path to the run directory"),
    adapter: str | None = typer.Option(None, "--adapter", help="Filter to one adapter.type"),  # noqa: UP007
    step: str | None = typer.Option(None, "--step", help="Filter to one step.id"),  # noqa: UP007
    engine: str | None = typer.Option(  # noqa: UP007
        None,
        "--engine",
        help="Filter to one search engine tool.name (google_web_search, filtered_web_search, ...)",
    ),
    project: list[str] | None = typer.Option(  # noqa: UP007
        None, "--project", help="Override default columns (repeatable or comma-separated)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit rows as JSON array"),
) -> None:
    """List web-search tool calls with search-centric defaults."""
    if not run_dir.is_dir():
        raise typer.BadParameter(f"run_dir not found: {run_dir}")

    spans = _load_trace(run_dir)
    flt = _build_filter(adapter=adapter, step=step, engine=engine)
    filtered = [span for span in apply_filter(spans, flt) if _is_search_like(span)]

    columns = _split_csv(project) or list(_DEFAULT_LIST_COLUMNS)
    rows = project_spans(filtered, columns)

    if as_json:
        typer.echo(json.dumps([dict(zip(columns, r, strict=False)) for r in rows], indent=2))
    else:
        typer.echo(format_projected_table(rows, columns), nl=False)
