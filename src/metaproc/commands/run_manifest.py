"""metaproc run-manifest — validate and normalize run manifest YAML.

Thin CLI wrapper over ``metaproc.models.run_manifest.RunManifest``. Useful
for the scaling-validation evidence trail: load an operator-authored
YAML, validate it against the Pydantic schema, and either print the
canonical form or rewrite the file in place.
"""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError

from metaproc.cli import app, get_output
from metaproc.models.run_manifest import RunManifest


@app.command("run-manifest")
def run_manifest(
    path: Path = typer.Argument(..., help="Path to a run manifest YAML file."),
    rewrite: bool = typer.Option(
        False,
        "--rewrite",
        help="Rewrite the file in canonical form instead of printing it.",
    ),
) -> None:
    """Validate a run manifest YAML; optionally rewrite it in canonical form."""
    out = get_output()
    try:
        manifest = RunManifest.from_yaml_file(path)
    except FileNotFoundError:
        out.data(f"Error: {path} does not exist")
        raise typer.Exit(code=1) from None
    except (ValidationError, ValueError) as exc:
        out.data(f"Error: {path} failed validation:\n{exc}")
        raise typer.Exit(code=1) from None

    canonical = manifest.to_yaml(path if rewrite else None)
    if rewrite:
        out.data(f"Rewrote {path} in canonical form.")
    else:
        out.data(canonical)
