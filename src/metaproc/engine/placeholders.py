"""Template placeholder resolution and validation."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from metaproc.config.env_vars import MetaprocEnv
from metaproc.models.authored import IOSpec, ProcessSpec, ProcessStep

# Framework-generated variables injected by the harness at runtime.
INTERNAL_DEFERRED = {
    "run.execution_profile",
    "run.artifact_namespace",
    "run.variant",
    "step.prompt_path",
    "step.prompt_paths",
    "step.outputs_list",
}

# Runtime variables imported from the process environment rather than declared as params.
ENV_RUNTIME_VARS = {"RUNS_DIR", "run.parent_dir"}

TEMPLATE_PATTERN = re.compile(r"\{\{([\w.]+)\}\}")
_RESERVED_FRAMEWORK_PREFIXES = frozenset({"run", "step"})
FRAMEWORK_TEMPLATE_VARS = frozenset(
    {
        "run.id",
        "run.dir",
        "run.parent_dir",
        "run.execution_profile",
        "run.artifact_namespace",
        "run.variant",
        "step.prompt_path",
        "step.prompt_paths",
        "step.outputs_list",
    }
)


class TemplateResolutionError(ValueError):
    """Raised when a placeholder resolves to a non-interpolable value."""


def extract_placeholders(text: str) -> list[str]:
    """Return placeholder names found within ``{{...}}`` tokens."""
    return re.findall(TEMPLATE_PATTERN, text)


def validate_framework_placeholder(name: str) -> str | None:
    """Validate dotted placeholder syntax against reserved framework namespaces."""
    if "." not in name:
        return None
    prefix, _, _member = name.partition(".")
    if prefix not in _RESERVED_FRAMEWORK_PREFIXES:
        return f"unknown framework namespace in {{{{{name}}}}} (reserved: run.*, step.*)"
    if name not in FRAMEWORK_TEMPLATE_VARS:
        return f"unknown framework variable {{{{{name}}}}}"
    return None


def _absolute_run_dir(parent: str, run_id: str) -> str:
    """Combine `parent` + `run_id` and return an absolute path string.

    Resolving to absolute means the rendered ``{{run.dir}}`` does not depend on
    where a downstream command happens to be running. Code-mode steps
    invoke their `command:` via ``subprocess.run(..., cwd=process_dir)``;
    agents fork their own subprocesses with their own cwds. A relative
    ``{{run.dir}}`` like ``relative-runs-root/<id>`` resolves relative
    to whatever cwd the subprocess inherits. Anchoring at the operator's cwd
    at template-resolution time makes the path stable for every consumer.
    """
    return str((Path(parent) / run_id).resolve())


def _lookup_template_value(key: str, variables: Mapping[str, object]) -> object | None:
    """Resolve exact keys first, then additive dotted aliases introduced in Phase 2A."""
    if key in variables:
        return variables[key]
    if "." not in key:
        return os.environ.get(key)

    if key == "run.parent_dir":
        if variables.get("RUNS_DIR"):
            return variables["RUNS_DIR"]
        runs_dir = MetaprocEnv.RUNS_DIR.read_path(default=None)
        return str(runs_dir) if runs_dir is not None else None
    if key == "run.id":
        return variables.get("RUN_ID") or MetaprocEnv.RUN_ID.read_str(default=None)
    if key == "run.execution_profile":
        return variables.get("EXECUTION_PROFILE")
    if key == "run.artifact_namespace":
        return (
            variables.get("ARTIFACT_NAMESPACE")
            or variables.get("VARIANT")
            or MetaprocEnv.VARIANT.read_str(default=None)
        )
    if key == "run.variant":
        return _lookup_template_value("run.artifact_namespace", variables)
    if key == "run.dir":
        parent = _lookup_template_value("run.parent_dir", variables)
        run_id = _lookup_template_value("run.id", variables)
        if parent is None or run_id is None:
            return None
        return _absolute_run_dir(str(parent), str(run_id))
    if key == "step.prompt_path":
        return variables.get("step.prompt_path")
    if key == "step.prompt_paths":
        return variables.get("step.prompt_paths")
    if key == "step.outputs_list":
        return variables.get("step.outputs_list")
    return None


def _stringify_template_value(key: str, value: object) -> str:
    """Render a resolved placeholder value into a string."""
    if isinstance(value, (list, tuple)):
        msg = f"cannot interpolate list value into {{{{{key}}}}}"
        raise TemplateResolutionError(msg)
    if isinstance(value, dict):
        msg = f"cannot interpolate map value into {{{{{key}}}}}"
        raise TemplateResolutionError(msg)
    return str(value)


def resolve_templates(text: str, variables: Mapping[str, object]) -> str:
    """Replace ``{{VAR}}`` placeholders with values from *variables*, then ``os.environ``.

    Resolution order for each placeholder:

    1. Explicit *variables* dict (from ``--var`` CLI args, system vars, etc.)
    2. Additive dotted aliases for 2A (``run.*``, ``step.*``)
    3. ``os.environ`` for flat names
    4. Left as-is (``{{VAR}}``) if neither source has the key.

    A placeholder that resolves to a present-but-empty value (``""`` or all
    whitespace) raises ``TemplateResolutionError`` rather than silently
    substituting nothing. This prevents a path such as
    ``outputs/{{run.variant}}/<item>`` from becoming
    ``outputs//<item>`` with a component missing. The "missing key" branch (None) is
    different: it leaves the literal ``{{KEY}}`` token in the output, which
    is visible and easy to debug. Empty-string substitution is the silent
    failure mode this guard exists to prevent.
    """

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = _lookup_template_value(key, variables)
        if value is None:
            return f"{{{{{key}}}}}"
        rendered = _stringify_template_value(key, value)
        if not rendered.strip():
            msg = (
                f"template placeholder {{{{{key}}}}} resolved to an empty string "
                f"in {text!r} — refusing to substitute and produce a malformed path. "
                f"Either set the variable to a non-empty value or omit it entirely "
                f"(unset variables leave the literal {{{{{key}}}}} token visible)."
            )
            raise TemplateResolutionError(msg)
        return rendered

    return re.sub(TEMPLATE_PATTERN, replace, text)


def resolve_runtime_config(value: object, variables: Mapping[str, object]) -> object:
    """Resolve template placeholders within adapter config structures."""
    if isinstance(value, str):
        return resolve_templates(value, variables)
    if isinstance(value, list):
        return [resolve_runtime_config(item, variables) for item in cast(list[object], value)]
    if isinstance(value, dict):
        typed_dict = cast(dict[str, object], value)
        return {
            str(key): resolve_runtime_config(item, variables) for key, item in typed_dict.items()
        }
    return value


def resolve_output_paths(
    outputs: dict[str, IOSpec],
    variables: Mapping[str, object],
) -> dict[str, str]:
    """Resolve output spec templates to concrete paths."""
    return {
        name: resolve_templates(spec.path, variables)
        for name, spec in outputs.items()
        if spec.path is not None
    }


def collect_deferred_variables(spec: ProcessSpec) -> set[str]:
    """Derive the set of variables resolved at fan-out time."""
    deferred = set(INTERNAL_DEFERRED)
    for step in spec.steps:
        for_each = step.for_each
        if not for_each:
            continue
        each = for_each.bind.strip()
        deferred.add(each)
        for field in for_each.bind_fields:
            deferred.add(str(field).strip())
        if each and not each.endswith("s"):
            deferred.add(f"{each}s")
    return deferred


def collect_step_runtime_placeholders(step_def: ProcessStep) -> set[str]:
    """Collect placeholders that may remain unresolved at per-item runtime."""
    allowed = set(INTERNAL_DEFERRED)
    for prompt_path in step_def.prompt_paths:
        matches = extract_placeholders(prompt_path)
        allowed.update(matches)
    for io_specs in (step_def.inputs, step_def.outputs):
        for spec in io_specs.values():
            if not spec.path:
                continue
            matches = extract_placeholders(spec.path)
            allowed.update(matches)
    if step_def.output_root:
        matches = extract_placeholders(step_def.output_root)
        allowed.update(matches)
    return allowed


def validate_spec_placeholders(
    spec: ProcessSpec,
    variables: Mapping[str, object],
) -> list[str]:
    """Check that all path-template placeholders in *spec* resolve at startup.

    Returns a list of error messages for placeholders that remain unresolved
    after merging *variables* with ``os.environ``. Deferred variables (fan-out
    item fields and ``step.*`` runtime placeholders) are allowed
    to remain — they are filled in later.

    This catches the common footgun where a required env var like ``RUNS_DIR``
    is unset: without this check, ``{{RUNS_DIR}}`` leaks into output paths as
    a literal directory name.
    """
    allowed = collect_deferred_variables(spec)
    errors: list[str] = []

    def _check(text: str | None, context: str) -> None:
        if not text:
            return
        resolved = resolve_templates(text, variables)
        errors.extend(check_unresolved_placeholders(resolved, context=context, allowed=allowed))

    for name, decl in spec.inputs.items():
        _check(decl.path, f"process input '{name}'")
    for name, dep in spec.deps.items():
        _check(dep.path, f"process dep '{name}'")
    for name, decl in spec.outputs.items():
        _check(decl.path, f"process output '{name}'")
    for step in spec.steps:
        _check(step.prompt_prefix, f"step '{step.id}' prompt_prefix")
        for prompt_path in step.prompt_paths:
            _check(prompt_path, f"step '{step.id}' prompt_paths")
        for name, io in step.inputs.items():
            _check(io.path, f"step '{step.id}' input '{name}'")
        for name, io in step.outputs.items():
            _check(io.path, f"step '{step.id}' output '{name}'")
        _check(step.output_root, f"step '{step.id}' output_root")
        for env_key, env_val in step.env.items():
            _check(env_val, f"step '{step.id}' env '{env_key}'")
    return errors


def check_unresolved_placeholders(
    text: str,
    *,
    context: str = "",
    allowed: set[str] | None = None,
) -> list[str]:
    """Find {{VAR}} placeholders remaining after variable resolution."""
    allowed_set = allowed or set()
    unresolved: list[str] = extract_placeholders(text)
    errors: list[str] = []
    for var in unresolved:
        if var not in allowed_set:
            where = f" in {context}" if context else ""
            framework_error = validate_framework_placeholder(var)
            if framework_error is not None:
                errors.append(f"{framework_error}{where}")
            else:
                errors.append(f"unresolved placeholder {{{{{var}}}}}{where}")
    return errors
