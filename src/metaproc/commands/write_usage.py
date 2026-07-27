"""metaproc write-usage — generate a usage.md report from log files."""

from __future__ import annotations

from pathlib import Path

import typer

from metaproc.cli import app, get_output
from metaproc.io import iter_artifact_paths, logical_path
from metaproc.logutil.parsing import LogFile
from metaproc.logutil.usage import load_pricing, write_usage_report


@app.command("write-usage")
def write_usage(
    phase_dir: Path = typer.Argument(
        ..., help="Phase directory containing variant subdirectories with .logs/"
    ),
    run_id: str = typer.Option("", "--run-id", help="Run ID (defaults to parent dir name)"),
    phase: str = typer.Option("", "--phase", help="Phase name (defaults to dir name)"),
    tool_events_glob: str = typer.Option(
        "",
        "--tool-events-glob",
        help=(
            "Glob for plugin-owned ResourceEvent logs relative to phase_dir. "
            "Defaults to '**/.logs/tools/*/resource-events.jsonl'."
        ),
    ),
) -> None:
    """Generate a usage.md report from log files in a phase directory.

    Scans all .jsonl files under the phase directory, extracts usage stats,
    and writes a usage.md report with YAML frontmatter + prose summary. When
    plugin-owned ResourceEvent logs are present under the phase dir, their
    per-tool stats are folded into the report's tool_profiles.
    """
    out = get_output()

    if not phase_dir.is_dir():
        out.data(f"Directory not found: {phase_dir}")
        raise typer.Exit(code=1)

    # Discover agent log files, excluding plugin-owned tool event streams.
    jsonl_files = [
        f for f in iter_artifact_paths(phase_dir, "**/*.jsonl") if not _is_tool_event_log(f)
    ]
    if not jsonl_files:
        out.data(f"No .jsonl files found in {phase_dir}")
        raise typer.Exit(code=1)

    # Parse each log file to extract usage stats.
    log_files: list[LogFile] = []
    for i, f in enumerate(jsonl_files):
        lf = LogFile(f, i)
        lf.read_new_events()
        if lf.done:
            lf.flush()
        log_files.append(lf)

    with_usage = sum(1 for lf in log_files if lf.usage_stats is not None)
    out.data(f"Found {len(log_files)} log files ({with_usage} with usage data)")

    # Discover plugin-owned ResourceEvent JSONL sessions.
    if tool_events_glob:
        tool_event_files = list(iter_artifact_paths(phase_dir, tool_events_glob))
    else:
        tool_event_files = list(
            iter_artifact_paths(phase_dir, "**/.logs/tools/*/resource-events.jsonl")
        )
    if tool_event_files:
        out.data(f"Found {len(tool_event_files)} external ResourceEvent sessions")

    # Resolve run_id and phase.
    if not run_id:
        run_id = phase_dir.parent.name
    if not phase:
        phase = phase_dir.name

    pricing = load_pricing()
    output_path = phase_dir / "usage.md"
    write_usage_report(
        output_path,
        run_id,
        phase,
        log_files,
        pricing,
        tool_event_files=tool_event_files or None,
        phase_dir=phase_dir,
    )
    out.data(f"Wrote {output_path}")


def _is_tool_event_log(path: Path) -> bool:
    path = logical_path(path)
    parts = path.parts
    return (
        len(parts) >= 4
        and parts[-4] == ".logs"
        and parts[-3] == "tools"
        and parts[-1] == ("resource-events.jsonl")
    )
