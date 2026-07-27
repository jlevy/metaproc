"""metaproc validate — check outputs and validate frontmatter/schemas."""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError
from ruamel.yaml import YAMLError
from softschema import ArtifactValidationResult, validate_artifact

from metaproc.cli import app, get_output
from metaproc.commands.helpers import (
    find_step_def,
    load_process_spec,
    parse_var_args,
    resolve_process_path,
)
from metaproc.engine.condition import output_is_active
from metaproc.engine.pathing import compute_run_dir, find_item_dir, glob_resolve_path
from metaproc.engine.placeholders import resolve_templates
from metaproc.engine.process_scope import expand_process_vars
from metaproc.errors import CLIError
from metaproc.io import FmFormatError, artifact_exists, resolve_existing_artifact
from metaproc.io.frontmatter import fmf_read_frontmatter_artifact, load_yaml_typed
from metaproc.plugins.discovery import get_plugin_registry


@app.command()
def validate(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
    step: str = typer.Option(..., "--step", help="Step ID to validate"),
    var: list[str] = typer.Option([], "--var", help="KEY=VALUE parameters"),
    each: str | None = typer.Option(None, "--each", help="Variable name for items"),  # noqa: UP007
    items: str | None = typer.Option(
        None, "--items", help="Comma-separated list of items to validate"
    ),  # noqa: UP007
    run_root: str | None = typer.Option(None, "--run-root", help="Override run root directory"),  # noqa: UP007
    cloud_runs_dir: str | None = typer.Option(  # noqa: UP007
        None,
        "--cloud-runs-dir",
        help="Cloud storage path (e.g. /mnt/filestore/runs) to check output existence instead of local",
    ),
) -> None:
    """Check expected outputs exist for completed steps."""
    out = get_output()

    process_path = resolve_process_path(process_spec)
    process_dir = process_path.parent

    spec = load_process_spec(process_path)
    variables = expand_process_vars(spec, parse_var_args(var), process_dir=process_dir)
    base_dir = Path()

    step_def = find_step_def(spec, step)
    for_each = step_def.for_each
    step_each = each or (for_each.bind if for_each and for_each.bind else None)

    # Determine the run directory
    run_dir: Path
    run_dir = Path(run_root) if run_root else compute_run_dir(spec, variables)

    if not run_dir.exists():
        raise CLIError(f"run directory not found: {run_dir}")

    # Non-fan-out step: just check outputs
    if not step_each:
        outputs = step_def.outputs
        if not outputs:
            out.progress(f"Step '{step}' has no declared outputs to validate.")
            return

        all_ok = True
        for name, io_spec in outputs.items():
            if not io_spec.path:
                continue
            resolved = glob_resolve_path(base_dir, io_spec.path, variables)
            if resolved and resolved.exists():
                out.data(f"  ok  {name}: {resolved}")
            else:
                out.data(f"  MISSING  {name}: {resolve_templates(io_spec.path, variables)}")
                all_ok = False

        if not all_ok:
            raise typer.Exit(code=1)
        return

    # Fan-out step: validate per item
    if not items and cloud_runs_dir:
        # Auto-discover items from local run dir (all items with .state/status.yaml)
        discovered: list[str] = []
        for vdir in sorted(run_dir.iterdir()) if run_dir.is_dir() else []:
            if not vdir.is_dir():
                continue
            for item_d in sorted(vdir.iterdir()):
                if item_d.is_dir() and (item_d / ".state" / "status.yaml").exists():
                    discovered.append(item_d.name)
            if discovered:
                break  # use items from first variant
        if not discovered:
            raise CLIError("No items found in run directory for cloud validation")
        item_list = discovered
    elif not items:
        raise CLIError(f"--items required for fan-out step '{step}' (each={step_each})")
    else:
        item_list = [s.strip() for s in items.split(",") if s.strip()]

    # Build expected file list from declared outputs, gating on condition:
    expected_files: list[str] = []
    output_schemas: dict[str, str] = {}
    output_formats: dict[str, str] = {}
    for io_spec in step_def.outputs.values():
        if io_spec.type == "raw":
            continue
        if not io_spec.path:
            continue
        if not output_is_active(io_spec.condition, variables):
            continue
        output_name = Path(io_spec.path).name
        if output_name:
            expected_files.append(output_name)
            schema = io_spec.schema_
            fmt = io_spec.format or ""
            if schema:
                output_schemas[output_name] = schema
            if fmt:
                output_formats[output_name] = fmt

    form_files = list(dict.fromkeys(expected_files))
    results: list[tuple[str, bool, list[str]]] = []

    # Load plugin registry for schema-aware validation
    softschema_registry = get_plugin_registry().softschemas

    # When cloud_runs_dir is set, check output files at the cloud path.
    # The cloud item dir mirrors the local layout: cloud_runs_dir/variant/item/
    cloud_base = Path(cloud_runs_dir) if cloud_runs_dir else None

    for item in item_list:
        # For cloud validation, resolve item dir relative to cloud base
        if cloud_base:
            item_dir = find_item_dir(cloud_base, item)
            if not item_dir:
                # Try variant-prefixed lookup: cloud_runs_dir contains variant subdirs
                # matching the local run_dir structure
                for vdir in sorted(cloud_base.iterdir()) if cloud_base.is_dir() else []:
                    if vdir.is_dir():
                        candidate = find_item_dir(vdir, item)
                        if candidate:
                            item_dir = candidate
                            break
        else:
            item_dir = find_item_dir(run_dir, item)
        if not item_dir:
            results.append((item, False, ["<directory not found>"]))
            continue

        missing: list[str] = []
        for fname in form_files:
            fpath = item_dir / fname
            if not artifact_exists(fpath):
                missing.append(fname)
                continue
            fpath = resolve_existing_artifact(fpath)
            if cloud_base:
                # Cloud mode: skip schema validation, just check existence
                pass
            elif fname in output_schemas:
                contract_id = output_schemas[fname]
                fmt = output_formats.get(fname, "")
                try:
                    if fmt == "frontmatter-md":
                        result = validate_artifact(
                            fpath,
                            contract_id=contract_id,
                            registry=softschema_registry,
                        )
                        if not result.ok:
                            raise ValueError(_format_artifact_validation_error(result))
                    else:
                        binding = softschema_registry.resolve(contract_id)
                        model_cls = binding.model if binding else None
                        if model_cls:
                            load_yaml_typed(fpath, model_cls)
                        else:
                            fmf_read_frontmatter_artifact(fpath)
                except (ValidationError, YAMLError, FmFormatError, ValueError, TypeError) as e:
                    missing.append(f"{fname} (invalid: {e})")
            elif output_formats.get(fname) == "frontmatter-md":
                try:
                    fmf_read_frontmatter_artifact(fpath)
                except (FmFormatError, YAMLError) as e:
                    missing.append(f"{fname} (invalid frontmatter: {e})")

        results.append((item, len(missing) == 0, missing))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)

    mode = f"cloud ({cloud_runs_dir})" if cloud_runs_dir else "local"
    out.data(f"\nValidation: {step} ({len(item_list)} items, {mode})")
    out.data(f"Forms expected: {', '.join(form_files)}")
    out.data("")

    for item, ok, missing in results:
        if ok:
            out.data(f"  ok  {item}")
        else:
            out.data(f"  FAIL  {item}: missing {', '.join(missing)}")

    out.data(f"\nPassed: {passed} | Failed: {failed}")

    if failed:
        raise typer.Exit(code=1)


def _format_artifact_validation_error(result: ArtifactValidationResult) -> str:
    errors = result.structural.errors or result.semantic.errors
    if not errors:
        return f"{result.contract_id}: validation failed"
    first = errors[0]
    kind = first.get("kind") or first.get("type") or "validation_error"
    message = first.get("message") or first.get("msg") or str(first)
    return f"{result.contract_id}: {kind}: {message}"
