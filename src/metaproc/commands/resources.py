"""metaproc resources — print the calling process's resource context.

Read of host CPU + RAM, container cgroup CPU + memory limits, OS rlimits,
Python runtime, and METAPROC_*/BATCH_* env. Useful for:

- Smoke-checking that a Batch dispatch actually right-sized
  ``compute_resource`` (the cgroup limit should match the VM, not the 2 GB
  default).
- Diagnosing OOM-kills locally (run inside a container, see what cgroup the
  process is actually in).
- Confirming an operator override (``METAPROC_GCP_TASK_MEMORY_MIB``) shows up.
"""

from __future__ import annotations

import json

import typer

from metaproc.cli import app, get_output
from metaproc.osutils.resource_context import (
    collect_resource_context,
    format_resource_context,
)
from metaproc.output import OutputFormat


@app.command()
def resources(
    json_output: bool = typer.Option(False, "--json", help="Emit the snapshot as JSON."),
) -> None:
    """Show the current process's host + cgroup + OS + runtime resource limits."""
    out = get_output()
    ctx = collect_resource_context()

    if json_output or out.format == OutputFormat.JSON:
        out.data(json.dumps(ctx.to_dict(), indent=2))
        return

    out.data(format_resource_context(ctx))
