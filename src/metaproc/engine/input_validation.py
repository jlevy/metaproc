"""Validate process-level ``inputs:`` declarations at run-process startup.

Phase 2A of the process-file-design-cleanups spec. Only fires for specs that
declare ``inputs:`` at the process level — silent no-op for every existing
in-repo spec that hasn't adopted the Phase 2A surface yet.
"""

from __future__ import annotations

import glob as glob_mod
from dataclasses import dataclass
from pathlib import Path

from metaproc.engine.parse_dispatch import ParseError, parse_input_file
from metaproc.engine.placeholders import resolve_templates
from metaproc.io import FmFormatError, fmf_read_frontmatter
from metaproc.models.authored import ProcessInput, ProcessOutput, ProcessSpec
from metaproc.models.plan import Plan
from metaproc.models.runtime import OutputFailure, OutputFailureKind

#: An input the operator supplies on the command line, e.g. ``--var TICKER=AAPL``.
INPUT_CLASS_PARAM = "param"
#: An input backed by a file on disk, which may be missing, unparsable, or the wrong shape.
INPUT_CLASS_FILE = "file"


def validate_process_inputs(
    spec: ProcessSpec,
    variables: dict[str, str],
    process_dir: Path,
) -> list[str]:
    """Return a list of human-readable error strings — empty means valid.

    *variables* must be pre-expanded via ``expand_process_vars``.
    """
    return [
        message
        for _input_class, message in classify_process_input_errors(spec, variables, process_dir)
    ]


def classify_process_input_errors(
    spec: ProcessSpec,
    variables: dict[str, str],
    process_dir: Path,
) -> list[tuple[str, str]]:
    """Return ``(input class, message)`` pairs in spec declaration order.

    Launch validation reports operator-supplied parameters and file-backed
    inputs as separate classes: one is fixed by adding a ``--var``, the other by
    producing a file, and an operator hitting both should see both.
    """
    errors: list[tuple[str, str]] = []
    for name, decl in spec.inputs.items():
        errors.extend(_check_input(name, decl, variables, process_dir))
    return errors


def _check_input(
    name: str,
    decl: ProcessInput,
    variables: dict[str, str],
    process_dir: Path,
) -> list[tuple[str, str]]:
    if decl.param is not None:
        return [
            (INPUT_CLASS_PARAM, message)
            for message in _check_param(name, decl.param, variables, required=decl.required)
        ]
    if decl.path is not None:
        return [
            (INPUT_CLASS_FILE, message)
            for message in _check_file(name, decl, variables, process_dir)
        ]
    return [(INPUT_CLASS_FILE, f"input {name!r}: must declare either 'param:' or 'path:'")]


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


@dataclass(frozen=True)
class ProcessOutputValidation:
    """Human errors and the artifact evidence available at the process boundary.

    Invalid declarations can have no resolved path. They retain their error without
    fabricating an artifact failure; failures of resolved artifacts carry both forms.
    """

    errors: tuple[str, ...]
    output_failures: tuple[OutputFailure, ...] = ()


def validate_process_outputs(
    spec: ProcessSpec,
    variables: dict[str, str],
    process_dir: Path,
    *,
    plan: Plan | None = None,
) -> list[str]:
    """Return a list of process-level output contract violations."""
    return list(validate_process_outputs_detailed(spec, variables, process_dir, plan=plan).errors)


def validate_process_outputs_detailed(
    spec: ProcessSpec,
    variables: dict[str, str],
    process_dir: Path,
    *,
    plan: Plan | None = None,
) -> ProcessOutputValidation:
    """Preserve resolved output paths and messages alongside the existing CLI errors."""
    errors: list[str] = []
    failures: list[OutputFailure] = []
    for name, decl in spec.outputs.items():
        result = _check_output(name, decl, variables, process_dir, plan=plan)
        errors.extend(result.errors)
        failures.extend(result.output_failures)
    return ProcessOutputValidation(tuple(errors), tuple(failures))


def _check_output(
    name: str,
    decl: ProcessOutput,
    variables: dict[str, str],
    process_dir: Path,
    *,
    plan: Plan | None,
) -> ProcessOutputValidation:
    if decl.ref is not None:
        if plan is None:
            return ProcessOutputValidation(
                (f"output {name!r}: internal error, plan is required to resolve 'ref:'",)
            )
        resolved_ref = _resolve_output_ref(name, decl.ref, plan)
        if isinstance(resolved_ref, str):
            return ProcessOutputValidation((resolved_ref,))
        resolved_path, resolved_format = resolved_ref
        format_errors = _check_output_path(
            name,
            resolved_path,
            decl.as_,
            decl.format or resolved_format,
        )
    else:
        if decl.path is None:
            return ProcessOutputValidation(
                (f"output {name!r}: must declare either 'path:' or 'ref:'",)
            )
        resolved = _resolve_declared_path(decl.path, variables, process_dir)
        format_errors = _check_output_path(name, resolved, decl.as_, decl.format)
    return ProcessOutputValidation(
        tuple(f"output {name!r}: {failure.message}" for failure in format_errors),
        tuple(format_errors),
    )


def _check_output_path(
    name: str,
    resolved: Path,
    declared_type: object,
    format_name: str | None,
) -> list[OutputFailure]:
    if (
        getattr(declared_type, "kind", None) == "list"
        and getattr(getattr(declared_type, "element", None), "kind", None) == "path"
    ):
        matches = [Path(match) for match in glob_mod.glob(str(resolved))]
        if not matches:
            return [
                OutputFailure(
                    output=name,
                    path=str(resolved),
                    kind=OutputFailureKind.missing,
                    message=f"no files matched: {resolved}",
                )
            ]
        return _check_output_format(name, matches, format_name)

    if not resolved.exists():
        return [
            OutputFailure(
                output=name,
                path=str(resolved),
                kind=OutputFailureKind.missing,
                message=f"path does not exist: {resolved}",
            )
        ]

    return _check_output_format(name, [resolved], format_name)


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


def _check_output_format(
    name: str, paths: list[Path], format_name: str | None
) -> list[OutputFailure]:
    if format_name != "frontmatter-md":
        return []

    errors: list[OutputFailure] = []
    for path in paths:
        try:
            fmf_read_frontmatter(path)
        except FmFormatError as exc:
            errors.append(
                OutputFailure(
                    output=name,
                    path=str(path),
                    kind=OutputFailureKind.unreadable,
                    message=f"{path}: invalid frontmatter: {exc}",
                )
            )
        except OSError as exc:
            errors.append(
                OutputFailure(
                    output=name,
                    path=str(path),
                    kind=OutputFailureKind.unreadable,
                    message=f"{path}: could not read output: {exc}",
                )
            )
    return errors
