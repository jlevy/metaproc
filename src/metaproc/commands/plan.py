"""metaproc plan — generate a resolved .plan.yaml from a .process.md spec."""

from __future__ import annotations

from pathlib import Path

import typer
from strif import atomic_output_file

from metaproc.cli import app, get_output
from metaproc.commands.helpers import (
    load_process_spec,
    parse_adapter_config,
    parse_var_args,
    resolve_process_path,
    seed_runtime_vars,
)
from metaproc.engine.build_plan import build_plan
from metaproc.engine.input_validation import validate_process_inputs
from metaproc.engine.placeholders import validate_spec_placeholders
from metaproc.engine.process_scope import expand_process_vars
from metaproc.errors import ValidationError
from metaproc.io import to_yaml_string
from metaproc.plugins.discovery import get_plugin_registry
from metaproc.viz import build_viz_model, render_html, render_svg
from metaproc.viz_loader import load_plan_bundle

_PLAN_FORMATS = ("yaml", "svg", "html")


@app.command()
def plan(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
    var: list[str] = typer.Option([], "--var", help="KEY=VALUE parameters"),
    adapter: str | None = typer.Option(None, "--adapter", help="Override adapter type"),  # noqa: UP007
    variant: str | None = typer.Option(None, "--variant", help="Override execution profile"),  # noqa: UP007
    artifact_namespace: str | None = typer.Option(  # noqa: UP007
        None,
        "--artifact-namespace",
        help="Artifact namespace for output grouping; defaults to --variant when set.",
    ),
    variant_file: list[Path] = typer.Option(
        [],
        "--variant-file",
        help="Additional execution-profile registry file (repeatable).",
    ),
    profile_file: list[Path] = typer.Option(
        [],
        "--profile-file",
        help="Additional execution-profile registry file (repeatable).",
    ),
    adapter_config: list[str] = typer.Option(
        [], "--adapter-config", help="KEY=VALUE adapter config overrides"
    ),
    output: Path | None = typer.Option(None, "--output", help="Output path"),  # noqa: UP007
    output_format: str = typer.Option(
        "yaml",
        "--format",
        help="Output format: yaml (default), svg, or html",
    ),
) -> None:
    """Generate a resolved plan YAML, or render it as a static SVG/HTML viz."""
    out = get_output()

    if output_format not in _PLAN_FORMATS:
        raise ValidationError(
            f"--format must be one of {', '.join(_PLAN_FORMATS)}, got {output_format!r}"
        )

    process_path = resolve_process_path(process_spec)
    process_dir = process_path.parent

    if output_format in {"svg", "html"}:
        variables = seed_runtime_vars(parse_var_args(var))
        bundle = load_plan_bundle(process_path, params=variables)
        viz = build_viz_model(bundle)
        decorators = get_plugin_registry().viz_decorators
        rendered = (
            render_svg(viz, decorators=decorators)
            if output_format == "svg"
            else render_html(viz, decorators=decorators)
        )
        if output:
            with atomic_output_file(output) as tmp_path:
                Path(tmp_path).write_text(rendered)
            out.progress(f"{output_format.upper()} written to {output}")
        else:
            out.data(rendered)
        return

    spec = load_process_spec(process_path)
    variables = seed_runtime_vars(parse_var_args(var))
    variables = expand_process_vars(spec, variables, process_dir=process_dir)
    config_overrides = parse_adapter_config(adapter_config)

    placeholder_errors = validate_spec_placeholders(spec, variables)
    if placeholder_errors:
        msg = (
            "unresolved placeholders in process spec (pass via --var or set env var):\n  "
            + "\n  ".join(placeholder_errors)
        )
        raise ValidationError(msg)

    input_errors = validate_process_inputs(spec, variables, process_dir)
    if input_errors:
        raise ValidationError("process input validation failed:\n  " + "\n  ".join(input_errors))

    try:
        resolved_plan = build_plan(
            spec,
            variables,
            process_path=process_path,
            adapter_override=variant or adapter,
            artifact_namespace=artifact_namespace,
            profile_files=[*variant_file, *profile_file],
            config_overrides=config_overrides or None,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc

    plan_data = {"plan": resolved_plan.model_dump(by_alias=True, exclude_none=True)}
    yaml_text = to_yaml_string(plan_data)

    if output:
        with atomic_output_file(output) as tmp_path:
            Path(tmp_path).write_text(yaml_text)
        out.progress(f"Plan written to {output}")
    else:
        out.data(yaml_text.rstrip())
