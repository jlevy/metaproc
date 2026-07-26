"""metaproc wait — block until a run reaches a terminal state."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from metaproc.cli import app, get_output
from metaproc.engine.run_status import wait_for_completion
from metaproc.output import OutputFormat


@app.command()
def wait(
    run_dir: Path = typer.Argument(..., help="Path to the run directory"),
    variant: str | None = typer.Option(None, "--variant", help="Filter to a specific variant"),  # noqa: UP007
    timeout: float | None = typer.Option(None, "--timeout", help="Maximum wait time in seconds"),  # noqa: UP007
    interval: float = typer.Option(10.0, "--interval", help="Polling interval in seconds"),
    format: str | None = typer.Option(None, "--format", help="Output format: text or json"),  # noqa: UP007
) -> None:
    """Wait for a run to reach a terminal state.

    Blocks until all items are completed/failed/cached (no running or pending),
    or until the timeout is reached.

    Exit codes: 0 = all completed, 1 = failures exist, 2 = timeout, 130 = interrupted.
    """
    out = get_output()
    use_json = (format == "json") or (out.format == OutputFormat.JSON)

    try:
        final_status, exit_code = wait_for_completion(
            run_dir,
            variant=variant,
            timeout=timeout,
            interval=interval,
        )
    except KeyboardInterrupt:
        raise SystemExit(130) from None

    if use_json:
        # Late import to avoid the import cycle: commands.status imports
        # cli, cli registers commands.wait at module load time.
        from metaproc.commands.status import (  # noqa: PLC0415 -- pre-existing local import; needs review
            _format_json,
        )

        json.dump(_format_json(final_status), sys.stdout, default=str, indent=2)
        sys.stdout.write("\n")
    else:
        totals = final_status.totals
        if exit_code == 2:
            print(
                f"Timeout reached. Progress: {totals.completed}/{totals.total} completed, "
                f"{totals.failed} failed, {totals.running} running, {totals.pending} pending"
            )
        elif exit_code == 1:
            print(f"Run finished with {totals.failed} failures out of {totals.total} items")
        else:
            print(f"All {totals.total} items completed successfully")

    raise SystemExit(exit_code)
