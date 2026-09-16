"""metaproc operations — read a run's operations summary and compare runs."""

from __future__ import annotations

import json
import sys
from enum import StrEnum
from pathlib import Path

import typer

from metaproc.cli import app, get_output
from metaproc.engine.operations_render import render_rollup_markdown, render_summary_markdown
from metaproc.engine.operations_rollup import build_operations_rollup
from metaproc.engine.operations_summary import (
    build_operations_summary,
    summary_metadata,
    write_operations_summary,
)
from metaproc.errors import CLIError
from metaproc.io import to_yaml_string

operations_app = typer.Typer(
    name="operations",
    help="Summarize what a run did: elapsed time, per-item chains, parallelism, retries, "
    "agents and resources.",
    no_args_is_help=True,
)
app.add_typer(operations_app)


class SummaryFormat(StrEnum):
    md = "md"
    yaml = "yaml"
    json = "json"


class RollupFormat(StrEnum):
    md = "md"
    json = "json"


@operations_app.command("summary")
def operations_summary(
    run_dir: Path = typer.Argument(..., help="Run directory (the run root, not a child scope)"),
    write: bool = typer.Option(
        False,
        "--write",
        help="Also write operations-summary.md and its schema sidecar into RUN_DIR.",
    ),
    fmt: SummaryFormat = typer.Option(
        SummaryFormat.md, "--format", help="Output format: md, yaml, or json."
    ),
) -> None:
    """Build the operations summary from evidence on disk and print it.

    Read-only unless --write is given. It never runs resource recovery and never writes
    a .jsonl, so it is safe on a finished run whose resource projections must stay put.
    """
    _require_run_dir(run_dir)
    summary = build_operations_summary(run_dir)
    if write:
        target = write_operations_summary(summary, run_dir)
        get_output().progress(f"Wrote {target}")
    if fmt is SummaryFormat.md:
        sys.stdout.write(render_summary_markdown(summary))
    elif fmt is SummaryFormat.yaml:
        sys.stdout.write(to_yaml_string(summary_metadata(summary)))
    else:
        sys.stdout.write(json.dumps(summary.model_dump(mode="json"), indent=2) + "\n")
    sys.stdout.flush()


@operations_app.command("rollup")
def operations_rollup(
    run_dirs: list[Path] = typer.Argument(..., help="Run directories to set side by side"),
    target_min_minutes: float = typer.Option(
        10.0, "--target-min-minutes", help="Lower bound of the per-item chain target."
    ),
    target_max_minutes: float = typer.Option(
        15.0, "--target-max-minutes", help="Upper bound of the per-item chain target."
    ),
    recompute: bool = typer.Option(
        False,
        "--recompute",
        help="Build every summary from evidence instead of reading operations-summary.md.",
    ),
    fmt: RollupFormat = typer.Option(
        RollupFormat.md, "--format", help="Output format: md or json."
    ),
) -> None:
    """Print one row per run: items, elapsed, setup, chain time against the target,
    cost and elapsed per item, concurrency, peak RSS, swap, and retries.

    A run's written operations-summary.md is used when its extractor version matches;
    otherwise the summary is built in memory. Nothing is written.
    """
    for run_dir in run_dirs:
        _require_run_dir(run_dir)
    try:
        rollup = build_operations_rollup(
            run_dirs,
            target_min_s=target_min_minutes * 60,
            target_max_s=target_max_minutes * 60,
            recompute=recompute,
        )
    except ValueError as exc:
        raise CLIError(str(exc)) from exc
    if fmt is RollupFormat.md:
        sys.stdout.write(render_rollup_markdown(rollup))
    else:
        sys.stdout.write(json.dumps(rollup.model_dump(mode="json"), indent=2) + "\n")
    sys.stdout.flush()


def _require_run_dir(run_dir: Path) -> None:
    if not run_dir.is_dir():
        raise CLIError(f"run directory not found: {run_dir}")
    if not (run_dir / ".state").is_dir():
        raise CLIError(f"not a run directory (no .state/): {run_dir}")
