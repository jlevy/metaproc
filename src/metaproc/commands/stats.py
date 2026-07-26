"""metaproc stats — unified run statistics."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer

from metaproc.cli import app
from metaproc.stats.engine import ALL_SECTIONS, compute_run_stats
from metaproc.stats.formatters import format_full


@app.command()
def stats(
    run_dir: Path = typer.Argument(..., help="Path to the run directory"),
    json_output: bool = typer.Option(False, "--json", help="Structured JSON output"),
    section: list[str] | None = typer.Option(  # noqa: UP007
        None,
        "--section",
        help="Sections to include (repeatable or comma-separated): progress,throughput,timing,pool,api,resources",
    ),
    variant: str | None = typer.Option(None, "--variant", help="Filter to a specific variant"),  # noqa: UP007
    worker: str | None = typer.Option(  # noqa: UP007
        None, "--worker", help="Filter to a specific worker (e.g., worker-0)"
    ),
    no_system: bool = typer.Option(False, "--no-system", help="Skip live system resource metrics"),
    watch: bool = typer.Option(False, "--watch", help="Re-render every --interval seconds"),
    interval: float = typer.Option(10.0, "--interval", help="Watch refresh interval in seconds"),
) -> None:
    """Show comprehensive run statistics.

    Superset of ``metaproc status`` — includes progress, throughput, timing
    distributions, pool health, API usage, and resource stats.
    """
    sections: frozenset[str] | None = None
    if section:
        parts: set[str] = set()
        for s in section:
            parts.update(p.strip() for p in s.split(",") if p.strip())
        unknown = parts - ALL_SECTIONS
        if unknown:
            raise typer.BadParameter(
                f"Unknown sections: {', '.join(sorted(unknown))}. "
                f"Valid: {', '.join(sorted(ALL_SECTIONS))}"
            )
        sections = frozenset(parts)

    def _render() -> None:
        run_stats = compute_run_stats(
            run_dir,
            sections=sections,
            variant=variant,
            worker=worker,
            include_system=not no_system,
        )
        if json_output:
            json.dump(
                run_stats.model_dump(mode="json"),
                sys.stdout,
                default=str,
                indent=2,
            )
            sys.stdout.write("\n")
        else:
            print(format_full(run_stats))

    if not watch:
        _render()
        return

    try:
        while True:
            # Clear screen (ANSI), then render
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.flush()
            _render()
            time.sleep(max(1.0, interval))
    except KeyboardInterrupt:
        pass
