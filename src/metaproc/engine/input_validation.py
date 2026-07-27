"""Validate process-level ``inputs:`` declarations at run-process startup.

Phase 2A of the process-file-design-cleanups spec. Only fires for specs that
declare ``inputs:`` at the process level — silent no-op for every existing
in-repo spec that hasn't adopted the Phase 2A surface yet.
"""

from __future__ import annotations

import glob as glob_mod
from pathlib import Path

from metaproc.engine.parse_dispatch import ParseError, parse_input_file
from metaproc.engine.placeholders import resolve_templates
from metaproc.io import FmFormatError, fmf_read_frontmatter
from metaproc.models.authored import ProcessInput, ProcessOutput, ProcessSpec
from metaproc.models.plan import Plan


def validate_process_inputs(
    spec: ProcessSpec,
    variables: dict[str, str],
    process_dir: Path,
) -> list[str]:
    """Return a list of human-readable error strings — empty means valid.

    *variables* must be pre-expanded via ``expand_process_vars``.
    """
    errors: list[str] = []
    for name, decl in spec.inputs.items():
        errors.extend(_check_input(name, decl, variables, process_dir))
    return errors


def _check_input(
    name: str,
    decl: ProcessInput,
    variables: dict[str, str],
    process_dir: Path,
) -> list[str]:
    if decl.param is not None:
        return _check_param(name, decl.param, variables, required=decl.required)
    if decl.path is not None:
        return _check_file(name, decl, variables, process_dir)
    return [f"input {name!r}: must declare either 'param:' or 'path:'"]


def _check_param(
    name: str,
    param: str,
    variables: dict[str, str],
    *,
    required: bool,
) -> list[str]:
    value = variables.get(name) or variables.get(param)
    if not required and (value is None or value == ""):
        return []
    if value is None or value == "":
        return [f"input {name!r}: operator-supplied parameter {param!r} is not set"]
    return []


def _check_file(
    name: str,
    decl: ProcessInput,
    variables: dict[str, str],
    process_dir: Path,
) -> list[str]:
    if decl.path is None:
        return [f"input {name!r}: internal error, path missing"]
    resolved = resolve_templates(decl.path, variables)
    path = Path(resolved)
    if not path.is_absolute():
        path = (process_dir / path).resolve()
    if decl.parse is None:
        if not path.exists():
            return [f"input {name!r}: file does not exist: {path}"]
        return []
    try:
        parse_input_file(path, decl.parse, decl.as_)
    except ParseError as exc:
        return [f"input {name!r}: {exc}"]
    return []


def validate_process_outputs(
    spec: ProcessSpec,
    variables: dict[str, str],
    process_dir: Path,
    *,
    plan: Plan | None = None,
) -> list[str]:
    """Return a list of process-level output contract violations."""
    errors: list[str] = []
    for name, decl in spec.outputs.items():
        errors.extend(_check_output(name, decl, variables, process_dir, plan=plan))
    return errors


def _check_output(
    name: str,
    decl: ProcessOutput,
    variables: dict[str, str],
    process_dir: Path,
    *,
    plan: Plan | None,
) -> list[str]:
    if decl.ref is not None:
        if plan is None:
            return [f"output {name!r}: internal error, plan is required to resolve 'ref:'"]
        resolved_ref = _resolve_output_ref(name, decl.ref, plan)
        if isinstance(resolved_ref, str):
            return [resolved_ref]
        resolved_path, resolved_format = resolved_ref
        format_errors = _check_output_path(
            resolved_path,
            decl.as_,
            decl.format or resolved_format,
        )
        return [f"output {name!r}: {error}" for error in format_errors]
    if decl.path is None:
        return [f"output {name!r}: must declare either 'path:' or 'ref:'"]

    resolved = _resolve_declared_path(decl.path, variables, process_dir)
    format_errors = _check_output_path(
        resolved,
        decl.as_,
        decl.format,
    )
    return [f"output {name!r}: {error}" for error in format_errors]


def _check_output_path(
    resolved: Path,
    declared_type: object,
    format_name: str | None,
) -> list[str]:
    if (
        getattr(declared_type, "kind", None) == "list"
        and getattr(getattr(declared_type, "element", None), "kind", None) == "path"
    ):
        matches = [Path(match) for match in glob_mod.glob(str(resolved))]
        if not matches:
            return [f"no files matched: {resolved}"]
        return _check_output_format(matches, format_name)

    if not resolved.exists():
        return [f"path does not exist: {resolved}"]

    return _check_output_format([resolved], format_name)


def _resolve_output_ref(
    output_name: str,
    ref: str,
    plan: Plan,
) -> tuple[Path, str | None] | str:
    step_id, sep, step_output_name = ref.partition(".")
    if not sep or not step_id or not step_output_name:
        return f"output {output_name!r}: invalid ref {ref!r} (expected '<step-id>.<output-name>')"

    step = next((candidate for candidate in plan.steps if candidate.step_id == step_id), None)
    if step is None:
        return f"output {output_name!r}: ref {ref!r} points to an unknown step"

    producer_output = step.outputs.get(step_output_name)
    if producer_output is None or not producer_output.path:
        available = ", ".join(sorted(step.outputs)) or "<none>"
        return (
            f"output {output_name!r}: ref {ref!r} points to an unknown output "
            f"(available: {available})"
        )

    return Path(resolve_templates(producer_output.path, plan.params)), producer_output.format


def _resolve_declared_path(
    path_template: str, variables: dict[str, str], process_dir: Path
) -> Path:
    resolved = resolve_templates(path_template, variables)
    path = Path(resolved)
    if path.is_absolute():
        return path
    return (process_dir / path).resolve()


def _check_output_format(paths: list[Path], format_name: str | None) -> list[str]:
    if format_name != "frontmatter-md":
        return []

    errors: list[str] = []
    for path in paths:
        try:
            fmf_read_frontmatter(path)
        except FmFormatError as exc:
            errors.append(f"{path}: invalid frontmatter: {exc}")
    return errors
