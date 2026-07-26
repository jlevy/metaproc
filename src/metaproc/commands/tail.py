"""metaproc tail — live tail of JSONL log files."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from prettyfmt import fmt_timedelta

from metaproc.cli import app
from metaproc.config.env_vars import MetaprocEnv
from metaproc.logutil.parsing import (
    COLORS,
    RESET,
    LogFile,
    discover_files,
    render_log_event,
    render_status_line,
)


def _print_tail_summary(files: dict[Path, LogFile]) -> None:
    """Print a per-file summary table."""
    print("\n--- Summary ---")
    print(f"{'File':<35} {'Adapter':<8} {'Status':<8} {'Duration':<12} {'Cost':<10}")
    print("-" * 75)
    for lf in sorted(files.values(), key=lambda f: f.label):
        adapter = lf.parser.adapter_name if lf.parser else "?"
        status = "FAIL" if lf.is_error else ("done" if lf.done else "active")
        dur_str = fmt_timedelta(lf.duration_s) if lf.duration_s is not None else ""
        cost_str = f"${lf.cost_usd:.2f}" if lf.cost_usd is not None else ""
        print(f"{lf.label:<35} {adapter:<8} {status:<8} {dur_str:<12} {cost_str:<10}")


@app.command()
def tail(
    dirs: list[Path] = typer.Argument(..., help="One or more .logs/ directories to watch"),
    once: bool = typer.Option(False, "--once", help="Read all content and exit"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable status line and colors"),
    poll_interval: float = typer.Option(2.0, "--poll", help="Poll interval in seconds"),
    kind_filter: str | None = typer.Option(None, "--filter", help="Show only events of this kind"),  # noqa: UP007
    summary: bool = typer.Option(False, "--summary", help="Print per-file summary on exit"),
) -> None:
    """Tail JSONL log files from .logs/ directories.

    Watches one or more .logs/ directories, parses Claude Code and Gemini CLI
    stream-json events, and renders a formatted, color-coded live view.

    Use --once for non-interactive review (read all content, print, exit).
    Use --once --summary for a quick status overview of a completed run.
    """
    use_color = (
        not no_progress
        and not MetaprocEnv.NO_COLOR.read_str(default=None)
        and sys.stdout.isatty()
        and sys.stderr.isatty()
    )

    files: dict[Path, LogFile] = {}
    color_counter = [0]

    for d in dirs:
        if not d.exists():
            typer.echo(f"Warning: {d} does not exist yet (will poll)", err=True)

    def _emit_events() -> None:
        for lf in list(files.values()):
            events = lf.read_new_events()
            for event in events:
                if kind_filter and event.kind != kind_filter:
                    continue
                rendered = render_log_event(event, use_color)
                prefix = lf.label.ljust(30)
                if use_color:
                    prefix = f"{COLORS[lf.color_idx]}{prefix}{RESET}"
                print(f"{prefix}  {rendered}", flush=True)

    def _flush_all() -> None:
        for lf in files.values():
            for event in lf.flush():
                if kind_filter and event.kind != kind_filter:
                    continue
                rendered = render_log_event(event, use_color)
                prefix = lf.label.ljust(30)
                if use_color:
                    prefix = f"{COLORS[lf.color_idx]}{prefix}{RESET}"
                print(f"{prefix}  {rendered}", flush=True)

    if once:
        discover_files(dirs, files, color_counter)
        _emit_events()
        _flush_all()
        if summary:
            _print_tail_summary(files)
        return

    typer.echo(f"Watching {len(dirs)} director{'y' if len(dirs) == 1 else 'ies'}...", err=True)

    try:
        while True:
            discover_files(dirs, files, color_counter)
            _emit_events()

            if use_color and files:
                status = render_status_line(files, use_color)
                print(f"\r{status}", end="", file=sys.stderr, flush=True)

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        _flush_all()

        if use_color:
            print("\r" + " " * 80 + "\r", end="", file=sys.stderr, flush=True)

        if summary:
            _print_tail_summary(files)

        typer.echo("\nStopped.", err=True)
