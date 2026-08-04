"""Validation helpers for process specs and runtime outputs."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ValidationError
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
from metaproc.io.frontmatter import fmf_read_frontmatter_artifact, load_yaml_typed
from metaproc.models.authored import IOSpec, ProcessSpec
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

    ``variables`` is used to render any per-item templates (e.g. ``{{item}}``)
    in output paths before checking existence. Without it, a for_each step
    whose output path contains a bind var will fail validation because
    ``Path("{{item}}").name`` compares as the literal placeholder.

    Directory-kind outputs must be non-empty — an empty directory indicates
    the step produced zero records and is treated as a silent-success failure
    (the classic ``mine-adhoc`` mode where an agent reports SUCCESS but wrote
    nothing under the item directory). ``.state/``, ``.logs/``, ``__pycache__``,
    and ``.DS_Store`` are ignored when counting content.

    Returns a list of error strings (empty = all valid).
    """
    if softschema_registry is None:
        softschema_registry = get_plugin_registry().softschemas
    errors: list[str] = []

    for io_spec in outputs.values():
        path_template = io_spec.path
        if not path_template:
            continue
        rendered = resolve_templates(path_template, variables) if variables else path_template
        fname = Path(rendered).name
        if io_spec.kind == "directory":
            fpath = _resolve_output_fpath(rendered, item_dir)
            # Preserve the fan-out convenience where a directory output's
            # rendered basename equals item_dir.name (e.g. output path
            # ``{{run.dir}}/.../{{item}}/`` against item_dir
            # ``.../<item>/``): treat item_dir itself as the target.
            if not fpath.exists() and item_dir.name == fname:
                fpath = item_dir
            if not fpath.exists() or not fpath.is_dir():
                errors.append(f"{fname}: directory not found")
                continue
            if not _directory_has_content(fpath):
                errors.append(f"{fname}: directory is empty (no output files produced)")
            continue
        fpath = _resolve_output_fpath(rendered, item_dir)
        if not fpath.exists() and not artifact_exists(fpath):
            errors.append(f"{fname}: file not found")
            continue
        if artifact_exists(fpath):
            fpath = resolve_existing_artifact(fpath)
        fmt = io_spec.format or ""
        schema = io_spec.schema_ or ""
        if fmt == "frontmatter-md" and schema:
            try:
                frontmatter = fmf_read_frontmatter_artifact(fpath)
            except FmFormatError as e:
                errors.append(f"{fname}: {e}")
                continue
            result = validate_artifact(
                fpath,
                contract_id=schema,
                registry=softschema_registry,
                document=frontmatter,
            )
            if not result.ok:
                errors.append(f"{fname}: {_format_artifact_validation_error(result)}")
        elif fmt == "frontmatter-md":
            try:
                fmf_read_frontmatter_artifact(fpath)
            except FmFormatError as e:
                errors.append(f"{fname}: {e}")
        elif schema:
            binding = softschema_registry.resolve(schema)
            model_cls = binding.model if binding else None
            if model_cls:
                try:
                    load_yaml_typed(fpath, model_cls)
                except (ValidationError, ValueError) as e:
                    errors.append(f"{fname}: {e}")
    return errors


def _format_artifact_validation_error(result: ArtifactValidationResult) -> str:
    errors = result.structural.errors or result.semantic.errors
    if not errors:
        return f"{result.contract_id}: validation failed"
    first = errors[0]
    kind = first.get("kind") or first.get("type") or "validation_error"
    message = first.get("message") or first.get("msg") or str(first)
    return f"{result.contract_id}: {kind}: {message}"


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
