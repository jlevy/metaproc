"""Validation helpers for process specs and runtime outputs."""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Mapping
from pathlib import Path

from frontmatter_format import read_yaml_file
from pydantic import BaseModel
from ruamel.yaml import YAMLError
from softschema import (
    ArtifactValidationResult,
    Contracts,
    validate_artifact,
)

from metaproc.engine.placeholders import (
    ENV_RUNTIME_VARS,
    FRAMEWORK_TEMPLATE_VARS,
    INTERNAL_DEFERRED,
    extract_placeholders,
    resolve_templates,
    validate_framework_placeholder,
)
from metaproc.engine.process_scope import is_dep_ref
from metaproc.io import FmFormatError, artifact_exists, resolve_existing_artifact
from metaproc.io.frontmatter import fmf_read_frontmatter_artifact
from metaproc.models.authored import IOSpec, ProcessSpec
from metaproc.models.runtime import OutputFailure, OutputFailureKind
from metaproc.plugins.discovery import get_plugin_registry

_RESERVED_SCOPE_NAMES: dict[str, str] = {
    "run_id": "run.id",
    "run_dir": "run.dir",
    "run_parent_dir": "run.parent_dir",
    "run_variant": "run.variant",
    "step_prompt_path": "step.prompt_path",
    "step_prompt_paths": "step.prompt_paths",
    "step_outputs_list": "step.outputs_list",
}


def validate_fan_out_contracts(spec: ProcessSpec, _context_path: Path) -> list[str]:
    """Check that fan-out placeholders are covered by params or declared item fields.

    Returns a list of error strings (empty = valid).
    """
    process_inputs = set(spec.inputs)
    errors: list[str] = []

    for step in spec.steps:
        for_each = step.for_each
        if not for_each:
            continue

        each = for_each.bind
        over = for_each.over
        if not each or not over:
            continue

        step_id = step.id
        if not is_dep_ref(over) and over not in step.inputs:
            errors.append(
                f"step '{step_id}': for_each.over '{over}' must match a declared step input or deps ref"
            )
            continue

        item_fields = {str(field).strip() for field in for_each.bind_fields}
        each_name = each.strip()
        allowed = process_inputs | item_fields | INTERNAL_DEFERRED | ENV_RUNTIME_VARS | {each_name}
        if each_name and not each_name.endswith("s"):
            allowed.add(f"{each_name}s")

        prompt = step.prompt_prefix or ""
        prompt_placeholders = extract_placeholders(prompt)
        for placeholder in prompt_placeholders:
            error = _placeholder_error(step_id, "prompt_prefix", placeholder, allowed)
            if error is not None:
                errors.append(error)

        for io_name, io_specs in (("inputs", step.inputs), ("outputs", step.outputs)):
            for spec_io in io_specs.values():
                if not spec_io.path:
                    continue
                io_placeholders = extract_placeholders(spec_io.path)
                for placeholder in io_placeholders:
                    error = _placeholder_error(step_id, io_name, placeholder, allowed)
                    if error is not None:
                        errors.append(error)
        if step.output_root:
            root_placeholders = extract_placeholders(step.output_root)
            for placeholder in root_placeholders:
                error = _placeholder_error(step_id, "output_root", placeholder, allowed)
                if error is not None:
                    errors.append(error)

    return errors


def _placeholder_error(
    step_id: str,
    location: str,
    placeholder: str,
    allowed: set[str],
) -> str | None:
    """Validate bare names against scope and dotted names against framework namespaces."""
    framework_error = validate_framework_placeholder(placeholder)
    if framework_error is not None:
        return f"step '{step_id}': {location} placeholder {{{{{placeholder}}}}} {framework_error}"
    if placeholder in FRAMEWORK_TEMPLATE_VARS or placeholder in allowed:
        return None
    return (
        f"step '{step_id}': {location} placeholder {{{{{placeholder}}}}} "
        "is not declared by params or item_fields"
    )


def validate_scope_collisions(spec: ProcessSpec) -> list[str]:
    """Reject authored names that would collide across process/item scopes."""
    process_scope = set(spec.inputs)
    errors: list[str] = []

    for name in sorted(process_scope):
        reserved_target = _RESERVED_SCOPE_NAMES.get(name)
        if reserved_target is not None:
            errors.append(
                f"process name '{name}' collides with reserved framework variable "
                f"'{{{{{reserved_target}}}}}'"
            )

    for step in spec.steps:
        for_each = step.for_each
        if for_each is None:
            continue

        item_scope = {for_each.bind, *for_each.bind_fields}
        collisions = sorted(process_scope & item_scope)
        errors.extend(
            f"step '{step.id}': scope collision for '{name}' between process scope and item scope"
            for name in collisions
        )

        for name in sorted(item_scope):
            reserved_target = _RESERVED_SCOPE_NAMES.get(name)
            if reserved_target is not None:
                errors.append(
                    f"step '{step.id}': item-scope name '{name}' collides with reserved "
                    f"framework variable '{{{{{reserved_target}}}}}'"
                )

    return errors


def validate_framework_template_names(spec: ProcessSpec) -> list[str]:
    """Reject dotted placeholders outside the reserved framework namespaces."""
    errors: list[str] = []

    def _check(text: str | None, context: str) -> None:
        if not text:
            return
        for placeholder in extract_placeholders(text):
            framework_error = validate_framework_placeholder(placeholder)
            if framework_error is not None:
                errors.append(f"{context}: {framework_error}")

    for name, decl in spec.inputs.items():
        _check(decl.path, f"process input '{name}'")
    for name, dep in spec.deps.items():
        _check(dep.path, f"process dep '{name}'")
    for name, decl in spec.outputs.items():
        _check(decl.path, f"process output '{name}'")
    for step in spec.steps:
        _check(step.prompt_prefix, f"step '{step.id}' prompt_prefix")
        _check(step.output_root, f"step '{step.id}' output_root")
        for prompt_path in step.prompt_paths:
            _check(prompt_path, f"step '{step.id}' prompt_paths")
        for env_key, env_val in step.env.items():
            _check(env_val, f"step '{step.id}' env '{env_key}'")
        for name, io_spec in step.inputs.items():
            _check(io_spec.path, f"step '{step.id}' input '{name}'")
        for name, io_spec in step.outputs.items():
            _check(io_spec.path, f"step '{step.id}' output '{name}'")
    return errors


_DIR_OUTPUT_EXCLUDED_NAMES: frozenset[str] = frozenset(
    {".state", ".logs", "__pycache__", ".DS_Store"}
)


def _directory_has_content(fpath: Path) -> bool:
    """Return True if ``fpath`` contains at least one non-state, non-log file."""
    for child in fpath.iterdir():
        if child.name in _DIR_OUTPUT_EXCLUDED_NAMES:
            continue
        if child.is_file():
            return True
        if child.is_dir() and _directory_has_content(child):
            return True
    return False


def normalize_for_structural_pass(value: object) -> object:
    """Convert values to the serialized form the schema describes.

    A JSON Schema compiled from a pydantic model describes the *serialized*
    document, where a ``date`` field is ``type: string``. YAML hands back a
    ``datetime.date`` for an unquoted ``2026-08-21``, so the same value fails
    unquoted and passes quoted, which no author can be expected to get right and
    YAML does not enforce.

    Only types with an unambiguous serialized form are converted. Anything else
    is passed through untouched, so a genuine type disagreement still fails.
    """
    match value:
        case datetime.datetime() | datetime.date() | datetime.time():
            return value.isoformat()
        case decimal.Decimal() | uuid.UUID():
            return str(value)
        case dict():
            return {k: normalize_for_structural_pass(v) for k, v in value.items()}
        case list():
            return [normalize_for_structural_pass(v) for v in value]
        case tuple():
            return [normalize_for_structural_pass(v) for v in value]
        case _:
            return value


def _resolve_output_fpath(rendered_path: str, item_dir: Path) -> Path:
    """Resolve a rendered output path against ``item_dir``.

    Absolute paths and multi-component relative paths (e.g.
    ``artifacts/index.yaml``) resolve as-is — repo-relative paths fall
    through to the process's cwd. Single-component names (e.g.
    ``prediction.md`` from a fan-out step) resolve under ``item_dir``.
    """
    p = Path(rendered_path)
    if p.is_absolute() or len(p.parts) > 1:
        return p
    return item_dir / p


def validate_item_outputs(
    item_dir: Path,
    outputs: dict[str, IOSpec],
    *,
    variables: Mapping[str, object] | None = None,
    softschema_registry: Contracts | None = None,
) -> list[str]:
    """Validate declared outputs exist in the item directory.

    Returns a list of error strings (empty = all valid). This is the string view
    of :func:`validate_item_outputs_detailed`; callers that need to know which
    invariant refused which output should use that instead of parsing these.
    """
    failures = validate_item_outputs_detailed(
        item_dir,
        outputs,
        variables=variables,
        softschema_registry=softschema_registry,
    )
    return [f.summary() for f in failures]


def validate_item_outputs_detailed(
    item_dir: Path,
    outputs: dict[str, IOSpec],
    *,
    variables: Mapping[str, object] | None = None,
    softschema_registry: Contracts | None = None,
) -> list[OutputFailure]:
    """Validate declared outputs, keeping what refused each one.

    ``variables`` is used to render any per-item templates (e.g. ``{{item}}``)
    in output paths before checking existence. Without it, a for_each step
    whose output path contains a bind var will fail validation because
    ``Path("{{item}}").name`` compares as the literal placeholder.

    Directory-kind outputs must be non-empty — an empty directory indicates
    the step produced zero records and is treated as a silent-success failure
    (the classic ``mine-adhoc`` mode where an agent reports SUCCESS but wrote
    nothing under the item directory). ``.state/``, ``.logs/``, ``__pycache__``,
    and ``.DS_Store`` are ignored when counting content.

    Every failing invariant is reported, not just the first.
    """
    if softschema_registry is None:
        softschema_registry = get_plugin_registry().softschemas
    failures: list[OutputFailure] = []

    for output_name, io_spec in outputs.items():
        path_template = io_spec.path
        if not path_template:
            continue
        rendered = resolve_templates(path_template, variables) if variables else path_template
        fname = Path(rendered).name
        schema = io_spec.contract or ""

        def fail(
            kind: OutputFailureKind,
            message: str,
            *,
            invariant: str | None = None,
            location: str | None = None,
            _name: str = output_name,
            _rendered: str = rendered,
            _schema: str = schema,
        ) -> None:
            failures.append(
                OutputFailure(
                    output=_name,
                    path=_rendered,
                    contract=_schema or None,
                    kind=kind,
                    invariant=invariant,
                    location=location,
                    message=message,
                )
            )

        if io_spec.kind == "directory":
            fpath = _resolve_output_fpath(rendered, item_dir)
            # Preserve the fan-out convenience where a directory output's
            # rendered basename equals item_dir.name (e.g. output path
            # ``{{run.dir}}/.../{{item}}/`` against item_dir
            # ``.../<item>/``): treat item_dir itself as the target.
            if not fpath.exists() and item_dir.name == fname:
                fpath = item_dir
            if not fpath.exists() or not fpath.is_dir():
                fail(OutputFailureKind.missing, "directory not found")
                continue
            if not _directory_has_content(fpath):
                fail(OutputFailureKind.empty, "directory is empty (no output files produced)")
            continue

        fpath = _resolve_output_fpath(rendered, item_dir)
        if not fpath.exists() and not artifact_exists(fpath):
            fail(OutputFailureKind.missing, "file not found")
            continue
        if artifact_exists(fpath):
            fpath = resolve_existing_artifact(fpath)
        fmt = io_spec.format or ""

        if not schema:
            # No contract to check against. A frontmatter document is still parsed,
            # because a file that does not parse is not an output.
            if fmt == "frontmatter-md":
                try:
                    fmf_read_frontmatter_artifact(fpath)
                except FmFormatError as e:
                    fail(OutputFailureKind.unreadable, str(e))
            continue

        # One contract check for every format. Only reading the document differs:
        # a frontmatter artifact validates its frontmatter, anything else its
        # document root. Sending both through validate_artifact is what makes a
        # declaration mean the same thing wherever it is written, including that
        # an unresolvable contract fails rather than passing silently.
        try:
            document = (
                fmf_read_frontmatter_artifact(fpath)
                if fmt == "frontmatter-md"
                else read_yaml_file(fpath)
            )
        except (FmFormatError, OSError, ValueError, YAMLError) as e:
            # A document that will not parse is an unreadable output, not a crash.
            # YAMLError is not a ValueError, so it needs naming: without it a
            # malformed artifact raises out of validation and takes the run with it
            # instead of failing its own step.
            fail(OutputFailureKind.unreadable, str(e))
            continue

        result = validate_artifact(
            fpath,
            contract_id=schema,
            registry=softschema_registry,
            document=normalize_for_structural_pass(document),
        )
        if not result.ok:
            failures.extend(_artifact_failures(result, output_name, rendered, schema))
    return failures


def _artifact_failures(
    result: ArtifactValidationResult,
    output_name: str,
    rendered: str,
    schema: str,
) -> list[OutputFailure]:
    """Turn a softschema result into one failure per refusing invariant.

    ``message`` reproduces the string this has always produced, so
    ``StatusRecord.error`` is unchanged. Everything beside it is the detail that
    used to be discarded: which pass refused the document, which validator, and
    where.
    """
    # Both passes read the same document, so a structurally invalid one draws
    # complaints from each about the same fields. Report the pass that refused
    # it first: once the shape is wrong the semantic verdict describes the same
    # defect a second time, and a consumer counting failures would count it
    # twice and could route the two copies to different owners.
    if result.structural.errors:
        entries: list[tuple[OutputFailureKind, dict[str, object]]] = [
            (OutputFailureKind.structural, e) for e in result.structural.errors
        ]
    else:
        entries = [(OutputFailureKind.semantic, e) for e in result.semantic.errors]
    if not entries:
        return [
            OutputFailure(
                output=output_name,
                path=rendered,
                contract=schema,
                kind=OutputFailureKind.structural,
                message=f"{result.contract_id}: validation failed",
            )
        ]

    failures: list[OutputFailure] = []
    for kind, entry in entries:
        detail = entry.get("kind") or entry.get("type") or "validation_error"
        message = entry.get("message") or entry.get("msg") or str(entry)
        # Structural errors report the refusing JSON Schema keyword; semantic
        # errors are pydantic's, where the error type is the closest thing to
        # a named invariant.
        invariant = entry.get("validator") or (
            entry.get("type") if kind is OutputFailureKind.semantic else None
        )
        failures.append(
            OutputFailure(
                output=output_name,
                path=rendered,
                contract=schema,
                kind=_input_error_kind(result, kind, str(detail)),
                invariant=str(invariant) if invariant else None,
                location=_error_location(entry),
                message=f"{result.contract_id}: {detail}: {message}",
            )
        )
    return failures


_INPUT_ERROR_CODES: frozenset[str] = frozenset({"artifact_unreadable", "artifact_invalid_utf8"})


def _input_error_kind(
    result: ArtifactValidationResult, kind: OutputFailureKind, detail: str
) -> OutputFailureKind:
    """Report an undecodable artifact as ``unreadable`` rather than ``structural``.

    softschema reports these as ``outcome: "input_error"``; the distinction is
    the difference between a document that says the wrong thing and one that
    could not be read at all.
    """
    if result.outcome == "input_error" and detail in _INPUT_ERROR_CODES:
        return OutputFailureKind.unreadable
    return kind


def _error_location(entry: Mapping[str, object]) -> str | None:
    """Render the document path an error points at, in either pass's vocabulary."""
    loc = entry.get("path")
    if loc is None:
        loc = entry.get("loc")
    if loc is None:
        return None
    if isinstance(loc, (list, tuple)):
        return ".".join(str(part) for part in loc) or None
    text = str(loc)
    return text or None


def _check_envelope_schema_match(
    envelope: object,
    declared_schema: str,
    fpath: Path,
    schema_envelope_mappings: dict[str, str],
) -> None:
    """Verify that a parsed envelope matches the declared schema."""
    expected_key = schema_envelope_mappings.get(declared_schema)
    if expected_key is None:
        return
    if not hasattr(envelope, expected_key):
        actual_keys = (
            [k for k in type(envelope).model_fields if not k.startswith("_")]
            if isinstance(envelope, BaseModel)
            else []
        )
        msg = (
            f"{fpath}: envelope mismatch — declared schema '{declared_schema}' "
            f"expects '{expected_key}:' envelope but found {actual_keys}"
        )
        raise ValueError(msg)
