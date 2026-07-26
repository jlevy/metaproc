"""metaproc override -- run-scoped operator escape hatch for dependency gates."""

from __future__ import annotations

from pathlib import Path

import typer

from metaproc.cli import app, get_output
from metaproc.commands.helpers import (
    load_process_spec,
    parse_var_args,
    resolve_process_path,
    seed_runtime_vars,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.engine.pathing import compute_run_dir
from metaproc.engine.process_scope import expand_process_vars
from metaproc.errors import CLIError
from metaproc.io.overrides import clear_override, upsert_override


@app.command()
def override(
    run_id: str = typer.Argument(..., help="Run ID (subdirectory under RUNS_DIR)"),
    step: str = typer.Argument(..., help="Step ID to override"),
    process: str = typer.Option(
        ...,
        "--process",
        help=(
            "Path to the process spec (.process.md file or its directory) "
            "whose run we are overriding. Required because process-level run "
            "directories vary in layout — the engine reads overrides from "
            "<run_dir>/.state/overrides.yaml where <run_dir> is computed from "
            "the spec's run.dir + variables. Without this argument the CLI "
            "cannot place the file where the engine will read it. "
            "Example: --process workflow_package/process/predict."
        ),
    ),
    var: list[str] = typer.Option(
        [],
        "--var",
        help=(
            "KEY=VALUE pairs needed to resolve {{run.dir}} and friends from "
            "the process spec. RUN_ID is auto-set from the positional arg."
        ),
    ),
    satisfied: bool = typer.Option(
        False, "--satisfied", help="Mark the step as satisfied for downstream gates"
    ),
    note: str = typer.Option("", "--note", help="Operator note explaining the override"),
    for_downstream: list[str] = typer.Option(
        [], "--for-downstream", help="Limit override to specific downstream step(s) (repeatable)"
    ),
    item: str | None = typer.Option(  # noqa: UP007
        None, "--item", help="Per-item override (persisted but not resolver-active in v1)"
    ),
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Remove the existing override for this step/item/for-downstream tuple",
    ),
) -> None:
    """Add, update, or remove a step override for a run.

    Without --clear, --satisfied is required (only action in v1).
    """
    out = get_output()

    runs_dir_str = MetaprocEnv.RUNS_DIR.read_str(default="")
    if not runs_dir_str:
        raise CLIError("RUNS_DIR is not set. Export RUNS_DIR before using metaproc override.")

    process_path = resolve_process_path(Path(process))
    spec = load_process_spec(process_path)

    variables = seed_runtime_vars(parse_var_args(var))
    variables.setdefault("RUN_ID", run_id)
    variables = expand_process_vars(spec, variables, process_dir=process_path.parent)

    run_dir = compute_run_dir(spec, variables)
    if not run_dir.is_dir():
        raise CLIError(
            f"Computed run directory does not exist: {run_dir}. "
            f"Confirm RUN_ID={run_id!r} matches an existing run for "
            f"process {spec.name!r}."
        )

    fd = for_downstream or None

    if clear:
        removed = clear_override(run_dir, step=step, item=item, for_downstream=fd)
        if removed:
            out.data(f"Cleared override for step {step!r}")
        else:
            out.data(f"No matching override found for step {step!r}")
        return

    if not satisfied:
        raise CLIError("--satisfied is required when not using --clear (only action in v1)")

    by = (
        MetaprocEnv.METAPROC_OPERATOR.read_str(default=None)
        or MetaprocEnv.USER.read_str(default=None)
        or MetaprocEnv.USERNAME.read_str(default=None)
        or "unknown"
    )

    upsert_override(
        run_dir,
        step=step,
        action="satisfied",
        item=item,
        for_downstream=fd,
        by=by,
        note=note,
    )
    out.data(f"Override applied: step {step!r} marked satisfied — {note!r}")
