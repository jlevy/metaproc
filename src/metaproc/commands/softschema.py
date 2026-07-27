"""Softschema inspection, validation, compilation, and structure reports."""

from __future__ import annotations

import importlib
from pathlib import Path

import typer
from pydantic import BaseModel
from softschema import ArtifactValidationResult, compile_model, validate_artifact
from strif import atomic_output_file

from metaproc.cli import app, get_output
from metaproc.commands.helpers import load_process_spec, resolve_process_path
from metaproc.engine.yaml_repair import repair_frontmatter_file
from metaproc.errors import CLIError
from metaproc.io import fmf_write
from metaproc.plugins.discovery import get_plugin_registry
from metaproc.structure_report import build_structure_report, inspect_frontmatter_path

softschema_app = typer.Typer(help="Inspect and validate softschema artifacts.")
app.add_typer(softschema_app, name="softschema")


@softschema_app.command("inspect")
def inspect(path: Path = typer.Argument(..., help="Path to inspect")) -> None:
    """Inspect one frontmatter artifact."""
    if not path.exists():
        raise CLIError(f"path not found: {path}")
    registry = get_plugin_registry().softschemas
    get_output().data(inspect_frontmatter_path(path, registry))


@softschema_app.command("repair")
def repair(path: Path = typer.Argument(..., help="Path to repair")) -> None:
    """Repair common LLM-generated YAML frontmatter errors in one artifact."""
    if not path.exists():
        raise CLIError(f"path not found: {path}")
    repaired = repair_frontmatter_file(path)
    get_output().data({"path": str(path), "repaired": repaired})


@softschema_app.command("validate")
def validate(
    path: Path = typer.Argument(..., help="Path to validate"),
    schema: str = typer.Option(..., "--schema", help="Softschema schema id"),
) -> None:
    """Validate one artifact by schema id."""
    if not path.exists():
        raise CLIError(f"path not found: {path}")
    result = validate_artifact(path, contract_id=schema, registry=get_plugin_registry().softschemas)
    payload = _artifact_result_payload(result)
    get_output().data(payload)
    if not result.ok:
        raise typer.Exit(code=1)


@softschema_app.command("compile")
def compile(
    model: str = typer.Argument(..., help="Model as module:ClassName"),
    out: Path = typer.Option(..., "--out", help="YAML schema sidecar path"),
    check: bool = typer.Option(False, "--check", help="Check committed sidecar drift"),
) -> None:
    """Compile a Pydantic model to a JSON-Schema YAML sidecar."""
    model_cls = _load_model(model)
    result = compile_model(model_cls, out, check_only=check)
    get_output().data(
        {
            "out_path": str(result.out_path),
            "drift": result.drift,
            "drift_diff": result.drift_diff,
        }
    )
    if result.drift:
        raise typer.Exit(code=1)


@app.command("structure-report")
def structure_report(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
    run_dir: Path | None = typer.Option(None, "--run-dir", help="Run directory for the report"),  # noqa: UP007
    out: Path | None = typer.Option(None, "--out", help="Write durable frontmatter-md report"),  # noqa: UP007
) -> None:
    """Generate a process structure report."""
    process_path = resolve_process_path(process_spec)
    spec = load_process_spec(process_path)
    report = build_structure_report(
        process_path=process_path,
        spec=spec,
        registry=get_plugin_registry().softschemas,
        run_dir=run_dir,
    )
    if out is not None:
        _write_structure_report(
            out,
            report.model_dump(by_alias=True, exclude_none=True, mode="json"),
        )
        get_output().summary(
            f"wrote {out}",
            record={"path": str(out), "artifacts": report.summary.artifacts},
        )
        return
    get_output().data(report.model_dump(by_alias=True, exclude_none=True, mode="json"))


def _load_model(model_ref: str) -> type[BaseModel]:
    module_name, sep, class_name = model_ref.partition(":")
    if not sep or not module_name or not class_name:
        raise CLIError("model must be MODULE:ClassName")
    module = importlib.import_module(module_name)
    model_cls = getattr(module, class_name)
    if not isinstance(model_cls, type) or not issubclass(model_cls, BaseModel):
        raise CLIError(f"{model_ref} is not a Pydantic BaseModel class")
    return model_cls


def _artifact_result_payload(result: ArtifactValidationResult) -> dict[str, object]:
    return {
        "path": str(result.path),
        "schema": result.contract_id,
        "ok": result.ok,
        "status": result.status.value,
        "profile": result.profile.value,
        "warnings": [warning.model_dump() for warning in result.warnings],
        "structural": {
            "ok": result.structural.ok,
            "engine": result.structural.engine,
            "errors": result.structural.errors,
        },
        "semantic": {
            "ok": result.semantic.ok,
            "errors": result.semantic.errors,
            "skipped_reason": result.semantic.skipped_reason,
        },
    }


def _write_structure_report(out: Path, report_payload: dict[str, object]) -> None:
    metadata = {
        "softschema": {
            "contract": "metaproc.structure_report.v1",
            "status": "enforced",
        },
        "structure_report": report_payload,
    }
    body = _render_structure_report_body(report_payload)
    with atomic_output_file(out, make_parents=True) as tmp_path:
        fmf_write(tmp_path, body, metadata)


def _render_structure_report_body(report_payload: dict[str, object]) -> str:
    summary = report_payload.get("summary")
    artifacts = report_payload.get("artifacts")
    lines = ["# Structure Report", ""]
    if isinstance(summary, dict):
        lines.extend(
            [
                f"- Artifacts: {summary.get('artifacts', 0)}",
                f"- Warnings: {summary.get('warnings', 0)}",
                "",
            ]
        )
    if isinstance(artifacts, list):
        lines.extend(
            ["| artifact | schema | status | stage | warnings |", "| --- | --- | --- | --- | --- |"]
        )
        for raw in artifacts:
            if not isinstance(raw, dict):
                continue
            warnings = raw.get("warnings") or []
            lines.append(
                "| {id} | {schema} | {status} | {stage} | {warnings} |".format(
                    id=raw.get("id", ""),
                    schema=raw.get("schema", ""),
                    status=raw.get("status", ""),
                    stage=raw.get("stage", ""),
                    warnings=len(warnings) if isinstance(warnings, list) else 0,
                )
            )
    return "\n".join(lines) + "\n"
