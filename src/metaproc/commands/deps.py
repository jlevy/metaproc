"""metaproc deps — inspect declared process deps with runtime state."""

from __future__ import annotations

from pathlib import Path

import typer

from metaproc.cli import app, get_output
from metaproc.commands.helpers import (
    load_process_spec,
    parse_adapter_config,
    parse_var_args,
    relpath,
    resolve_process_path,
    seed_runtime_vars,
)
from metaproc.engine.build_plan import build_plan
from metaproc.engine.dep_state import build_dep_report
from metaproc.engine.input_validation import validate_process_inputs
from metaproc.engine.pathing import compute_run_dir
from metaproc.engine.placeholders import validate_spec_placeholders
from metaproc.engine.process_scope import expand_process_vars
from metaproc.errors import ValidationError
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import ResolvedDep
from metaproc.output import OutputFormat
from metaproc.plugins.discovery import get_plugin_registry
from metaproc.structure_report import StructureReportArtifact, build_structure_report


def _dep_payload(
    dep: ResolvedDep,
    artifact: StructureReportArtifact | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "path": dep.path,
        "state": dep.state or "",
        "produced_by": dep.produced_by,
        "consumers": dep.consumers,
    }
    if artifact is not None:
        payload["format"] = artifact.format
        payload["schema"] = artifact.contract_id
        payload["softschema_status"] = artifact.status.value
        payload["softschema_stage"] = artifact.stage.value
        payload["softschema_profile"] = artifact.profile.value
        payload["softschema_warnings"] = [warning.code for warning in artifact.warnings]
    return payload


def _format_dep_table(
    process_name: str,
    run_dir: Path,
    deps: dict[str, ResolvedDep],
    structures: dict[str, StructureReportArtifact],
) -> str:
    rows: list[tuple[str, str, str, str, str, str, str, str]] = []
    for dep_name, dep in deps.items():
        artifact = structures.get(dep_name)
        rows.append(
            (
                dep_name,
                dep.state or "",
                dep.produced_by or "-",
                artifact.contract_id if artifact and artifact.contract_id else "-",
                artifact.status.value if artifact else "-",
                artifact.stage.value if artifact else "-",
                ",".join(dep.consumers) if dep.consumers else "-",
                str(relpath(Path(dep.path))),
            )
        )

    header = ("Dep", "State", "Produced By", "Schema", "Status", "Stage", "Consumers", "Path")
    widths = [len(label) for label in header]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row, strict=True)]

    lines = [f"Process: {process_name}", f"Run dir: {relpath(run_dir)}", ""]
    lines.append("  ".join(label.ljust(width) for label, width in zip(header, widths, strict=True)))
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True))
        for row in rows
    )
    return "\n".join(lines)


def _dep_structure_map(
    spec: ProcessSpec,
    process_path: Path,
) -> dict[str, StructureReportArtifact]:
    report = build_structure_report(
        process_path=process_path,
        spec=spec,
        registry=get_plugin_registry().softschemas,
    )
    by_artifact_id = {artifact.id: artifact for artifact in report.artifacts}
    by_step: dict[str, list[StructureReportArtifact]] = {}
    for artifact in report.artifacts:
        if artifact.producer_step:
            by_step.setdefault(artifact.producer_step, []).append(artifact)
    mapped: dict[str, StructureReportArtifact] = {}
    for dep_name, dep in spec.deps.items():
        if dep.produced_by and dep.produced_by in by_artifact_id:
            mapped[dep_name] = by_artifact_id[dep.produced_by]
        elif dep.produced_by and len(by_step.get(dep.produced_by, [])) == 1:
            mapped[dep_name] = by_step[dep.produced_by][0]
    return mapped


@app.command("deps")
def deps(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
    var: list[str] = typer.Option([], "--var", help="KEY=VALUE parameters"),
    adapter: str | None = typer.Option(None, "--adapter", help="Override adapter type"),  # noqa: UP007
    adapter_config: list[str] = typer.Option(
        [], "--adapter-config", help="KEY=VALUE adapter config overrides"
    ),
) -> None:
    """Print declared deps with inferred runtime state for a planned run."""
    out = get_output()

    process_path = resolve_process_path(process_spec)
    spec = load_process_spec(process_path)
    variables = seed_runtime_vars(parse_var_args(var))
    variables = expand_process_vars(spec, variables, process_dir=process_path.parent)
    config_overrides = parse_adapter_config(adapter_config)

    placeholder_errors = validate_spec_placeholders(spec, variables)
    if placeholder_errors:
        msg = (
            "unresolved placeholders in process spec (pass via --var or set env var):\n  "
            + "\n  ".join(placeholder_errors)
        )
        raise ValidationError(msg)

    input_errors = validate_process_inputs(spec, variables, process_path.parent)
    if input_errors:
        raise ValidationError("process input validation failed:\n  " + "\n  ".join(input_errors))

    try:
        resolved_plan = build_plan(
            spec,
            variables,
            process_path=process_path,
            adapter_override=adapter,
            config_overrides=config_overrides or None,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc

    run_dir = compute_run_dir(spec, variables)
    dep_report = build_dep_report(resolved_plan, run_dir)
    dep_structures = _dep_structure_map(spec, process_path)

    if out.format == OutputFormat.JSON:
        out.data(
            {
                "process": spec.name,
                "process_path": str(process_path),
                "run_dir": str(run_dir),
                "deps": {
                    name: _dep_payload(dep, dep_structures.get(name))
                    for name, dep in dep_report.items()
                },
            }
        )
        return

    out.data(_format_dep_table(spec.name, run_dir, dep_report, dep_structures))
