"""Helpers for declared process inputs and process-level deps."""

from __future__ import annotations

from pathlib import Path

from metaproc.engine.placeholders import resolve_templates
from metaproc.models.authored import ProcessSpec


def expand_process_vars(
    spec: ProcessSpec,
    variables: dict[str, str],
    *,
    process_dir: Path | None = None,  # noqa: ARG001  (kept for caller API stability)
) -> dict[str, str]:
    """Expand declared ``param:`` aliases onto the runtime variable scope.

    Optional, param-backed inputs with a literal ``default:`` are populated when
    the operator does not supply a value.
    """
    expanded = spec.expand_param_aliases(variables)
    _apply_literal_defaults(spec, expanded)
    return expanded


def _apply_literal_defaults(spec: ProcessSpec, variables: dict[str, str]) -> None:
    """Fill in optional, param-backed inputs that declare ``default:``."""
    for logical_name, decl in spec.inputs.items():
        if decl.required or decl.param is None or decl.default is None:
            continue
        value = _first_non_blank(variables.get(logical_name), variables.get(decl.param))
        if value is not None:
            continue
        variables[logical_name] = decl.default
        variables[decl.param] = decl.default


def _first_non_blank(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value.strip():
            return value
    return None


def resolve_process_dep_path(
    path_template: str,
    variables: dict[str, str],
    process_dir: Path,
) -> str:
    """Resolve a process-level dep path.

    Relative dep paths are interpreted from the owning package root so authored
    ``deps:`` entries can consistently use package-root paths like
    ``process/predict/predict.process.md`` or ``src/example_plugin/...``.
    """

    resolved = resolve_templates(path_template, variables)
    path = Path(resolved)
    if path.is_absolute():
        return str(path)
    if "{{run." in path_template or _looks_like_run_relative(path_template):
        return resolved
    if path_template.startswith(("./", "../")):
        return str((process_dir / path).resolve())
    return str((_find_package_root(process_dir) / path).resolve())


def _looks_like_run_relative(path_template: str) -> bool:
    """Return whether the path template looks like it's relative to the run root.

    Run-relative paths should be returned as-is, not resolved against the process
    directory or package root.  The heuristic checks for a leading variable-like
    segment (e.g. ``v{{form_version}}/...``) or any ``{{`` in the first segment,
    indicating per-run resolution.
    """
    first_segment = path_template.split("/", 1)[0]
    return "{{" in first_segment


def _find_package_root(process_dir: Path) -> Path:
    """Return the nearest ancestor that looks like a package/workspace root."""
    current = process_dir.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def is_dep_ref(ref: str) -> bool:
    """Return whether *ref* names a process-level dep."""
    return ref.startswith("deps.") and len(ref) > len("deps.")


def parse_dep_ref(ref: str, *, context: str) -> str:
    """Extract the dep name from ``deps.<name>``."""
    if not is_dep_ref(ref):
        msg = f"{context}: invalid dep ref {ref!r} (expected 'deps.<name>')"
        raise ValueError(msg)
    return ref.split(".", 1)[1]
