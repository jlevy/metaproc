"""Parse dispatch for process-level inputs.

Given a file path, a :class:`~metaproc.models.authored.ParseConfig`, and a
declared :class:`~metaproc.models.authored.ValueType`, read the file and
return a Python value that conforms to the type.

Used by the Phase 2A process-level ``inputs:`` machinery.
Spec: src/metaproc/docs/metaproc-design.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAMLError

from metaproc.io import fmf_read_frontmatter, read_yaml_file
from metaproc.models.authored import ParseConfig, ValueType


class ParseError(Exception):
    """Raised when a declared process-level input cannot be parsed."""


def parse_input_file(path: Path, parse: ParseConfig, as_type: ValueType) -> object:
    """Read ``path`` per ``parse`` config and validate it against ``as_type``."""
    if not path.exists():
        msg = f"input file does not exist: {path}"
        raise ParseError(msg)

    raw = _dispatch_format(path, parse)
    _check_shape(raw, as_type, path=path)
    return raw


def _dispatch_format(path: Path, parse: ParseConfig) -> object:
    if parse.format == "yaml":
        return _parse_yaml(path)
    if parse.format == "frontmatter-md":
        return _parse_frontmatter_md(path, extract=parse.extract)
    msg = f"unsupported parse format: {parse.format!r}"
    raise ParseError(msg)


def _parse_yaml(path: Path) -> object:
    try:
        return read_yaml_file(path)
    except YAMLError as exc:
        msg = f"{path}: invalid YAML: {exc}"
        raise ParseError(msg) from exc


def _parse_frontmatter_md(path: Path, *, extract: str | None) -> object:
    raw = fmf_read_frontmatter(path)
    if raw is None:
        msg = f"{path}: no frontmatter block found"
        raise ParseError(msg)
    if not isinstance(raw, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        msg = f"{path}: frontmatter is not a mapping"
        raise ParseError(msg)
    if extract is None:
        return raw
    return _resolve_extract(raw, extract, path=path)


def _resolve_extract(root: dict[str, Any], extract: str, *, path: Path) -> object:
    """Locate ``extract`` either at top level or one envelope deep."""
    if extract in root:
        return root[extract]
    for v in root.values():
        if isinstance(v, dict) and extract in v:
            return v[extract]
    msg = f"{path}: frontmatter has no {extract!r} entry"
    raise ParseError(msg)


def _check_shape(
    value: object,
    as_type: ValueType,
    *,
    path: Path,
    location: str = "",
) -> None:
    kind = as_type.kind
    if kind == "list":
        if not isinstance(value, list):
            msg = _shape_error(path, location, "list", value)
            raise ParseError(msg)
        if as_type.element is not None:
            for index, item in enumerate(value):
                _check_shape(item, as_type.element, path=path, location=f"{location}[{index}]")
        return
    if kind == "map":
        if not isinstance(value, dict):
            msg = _shape_error(path, location, "map", value)
            raise ParseError(msg)
        if as_type.key is not None:
            for key in value:
                _check_shape(key, as_type.key, path=path, location=f"{location}.<key>")
        if as_type.value is not None:
            for key, item in value.items():
                suffix = _format_map_key(key)
                _check_shape(item, as_type.value, path=path, location=f"{location}{suffix}")
        return
    if kind == "string":
        if not isinstance(value, str):
            msg = _shape_error(path, location, "string", value)
            raise ParseError(msg)
        return
    # ``path`` values come back as strings from yaml/frontmatter dispatch.
    if kind == "path" and not isinstance(value, str):
        msg = _shape_error(path, location, "path (string)", value)
        raise ParseError(msg)


def _shape_error(path: Path, location: str, expected: str, value: object) -> str:
    target = f"{path}{location}" if location else str(path)
    return f"{target}: expected {expected}, got {type(value).__name__}"


def _format_map_key(key: object) -> str:
    if isinstance(key, str) and key.isidentifier():
        return f".{key}"
    return f"[{key!r}]"
