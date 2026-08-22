"""Make an agent-authored document say the types its contract asks for.

YAML plain scalars carry no type marker: ``1850`` is an integer because of how
the characters look, not because anyone said so. A program serializing a known
value never hits this, because every YAML emitter quotes a string that would
otherwise resolve to something else. An agent writing the document by hand has
no serializer in the path, so a brand genuinely named ``1850`` arrives as an
integer and fails a ``type: string`` contract.

This module puts the missing step back. Given the contract's JSON Schema, it
quotes the scalars the schema calls strings, in place, using the scalar's own
source text rather than ``str()`` of the parsed value: ``1.10`` stays ``1.10``
rather than becoming ``1.1``, and ``007`` stays ``007``.

**One direction only.** A scalar becomes a string where the schema asks for a
string. Nothing becomes a number, nothing changes shape, and a document that
disagrees with its contract in any other way still fails. Reading prose where
the schema wants a number is a real defect and stays one; reading ``1850`` where
the schema wants a name is a notation accident and is not worth a ticker.

**The edit is surgical.** Only the offending scalars are rewritten, at the
positions ruamel recorded for them, so a one-character fix produces a
one-character diff. Re-emitting the whole document would reformat indentation
and collapse ``null`` to empty, which buries the real change in noise and churns
every published artifact it touches.
"""

from __future__ import annotations

import datetime
import io
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError as RuamelYAMLError

from metaproc.engine.placeholders import resolve_templates
from metaproc.engine.validation import resolve_output_fpath

# Scalars a YAML document can produce where a schema asked for a string.
# ``None`` is deliberately absent: a null is an absent value, not a notation
# accident, and quoting it would invent data.
_COERCIBLE = (bool, int, float, datetime.date, datetime.time, datetime.datetime)


@dataclass(frozen=True)
class _Edit:
    line: int
    col: int
    old: str
    new: str


def _rt_yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    return yaml


def _source_text(yaml: YAML, value: object) -> str:
    """The scalar exactly as written, which ``str()`` does not give.

    ruamel's round-trip scalars remember their notation, so emitting one and
    reading the result back recovers ``1.10``, ``007``, ``0x1F`` and ``1e3``
    verbatim. ``str()`` collapses all four.
    """
    buf = io.StringIO()
    yaml.dump({"k": value}, buf)
    return buf.getvalue().split("k:", 1)[1].strip()


def _deref(schema: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any]:
    """Follow a local ``$ref``. A reference we cannot resolve yields no
    coercion rather than a wrong one."""
    seen = 0
    while "$ref" in schema and seen < 10:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return schema
        node: Any = root
        for part in ref[2:].split("/"):
            if not isinstance(node, Mapping) or part not in node:
                return schema
            node = node[part]
        if not isinstance(node, Mapping):
            return schema
        schema = node
        seen += 1
    return schema


def _types(schema: Mapping[str, Any]) -> set[str]:
    declared = schema.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, Sequence):
        return {t for t in declared if isinstance(t, str)}
    return set()


def _branches(schema: Mapping[str, Any], root: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The alternatives of an anyOf/oneOf, dereferenced. ``str | None`` is the
    common one, and it is why a null must not be read as "wants a string"."""
    for key in ("anyOf", "oneOf"):
        alts = schema.get(key)
        if isinstance(alts, Sequence) and not isinstance(alts, str | bytes):
            return [_deref(a, root) for a in alts if isinstance(a, Mapping)]
    return []


def _accepts_current_type(
    value: object, schema: Mapping[str, Any], root: Mapping[str, Any]
) -> bool:
    """Whether the schema already accepts this value's type, in which case
    quoting it would override a choice the contract deliberately offered."""
    if isinstance(value, bool):
        names = {"boolean"}
    elif isinstance(value, int):
        names = {"integer", "number"}
    elif isinstance(value, float):
        names = {"number"}
    else:
        return False
    for branch in _branches(schema, root) or [schema]:
        if _types(branch) & names:
            return True
    return False


def _wants_string(schema: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
    if "string" in _types(schema):
        return True
    return any("string" in _types(b) for b in _branches(schema, root))


def _child_schemas(
    schema: Mapping[str, Any], root: Mapping[str, Any], key: str
) -> Mapping[str, Any] | None:
    """The schema for one property, looking through branches for the object
    shape a ``Foo | None`` union hides."""
    for source in (schema, *_branches(schema, root)):
        props = source.get("properties")
        if not isinstance(props, Mapping):
            continue
        candidate = props.get(key)
        if isinstance(candidate, Mapping):
            return _deref(candidate, root)
    return None


def _item_schema(schema: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any] | None:
    items = schema.get("items")
    if isinstance(items, Mapping):
        return _deref(items, root)
    for branch in _branches(schema, root):
        cand = branch.get("items")
        if isinstance(cand, Mapping):
            return _deref(cand, root)
    return None


def _position(node: object, locator: str, key: object) -> tuple[int, int] | None:
    """Where ruamel recorded this child in the source.

    ``lc`` is real on round-trip containers but absent from ruamel's type stubs,
    and it raises for a key it never saw. Both cases mean "no position", which
    means no edit.
    """
    lc = getattr(node, "lc", None)
    method = getattr(lc, locator, None) if lc is not None else None
    if method is None:
        return None
    try:
        pos = method(key)
    except (KeyError, IndexError, AttributeError, TypeError):
        return None
    if isinstance(pos, tuple) and len(pos) >= 2:
        line, col = pos[0], pos[1]
        if isinstance(line, int) and isinstance(col, int):
            return line, col
    return None


def _record(
    yaml: YAML,
    value: object,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    pos: tuple[int, int] | None,
    edits: list[_Edit],
) -> None:
    if pos is None:
        return
    if not isinstance(value, _COERCIBLE) or isinstance(value, str):
        return
    if not _wants_string(schema, root) or _accepts_current_type(value, schema, root):
        return
    text = _source_text(yaml, value)
    edits.append(_Edit(line=pos[0], col=pos[1], old=text, new=f"'{text}'"))


def _walk(
    node: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    yaml: YAML,
    edits: list[_Edit],
) -> None:
    schema = _deref(schema, root)

    if isinstance(node, MutableMapping):
        for key in list(node.keys()):
            if not isinstance(key, str):
                continue
            sub = _child_schemas(schema, root, key)
            if sub is None:
                continue
            child = node[key]
            if isinstance(child, MutableMapping | list):
                _walk(child, sub, root, yaml, edits)
            else:
                pos = _position(node, "value", key)
                _record(yaml, child, sub, root, pos, edits)
        return

    if isinstance(node, list):
        sub = _item_schema(schema, root)
        if sub is None:
            return
        for index, child in enumerate(node):
            if isinstance(child, MutableMapping | list):
                _walk(child, sub, root, yaml, edits)
            else:
                pos = _position(node, "item", index)
                _record(yaml, child, sub, root, pos, edits)


def _envelope_key(document: Any, explicit: str | None) -> str | None:
    """The key the payload lives under, from the caller or the document itself.

    A softschema artifact names its own envelope in its ``softschema`` block, so
    a standalone caller does not need the registry to find the payload. The
    schema describes the envelope's contents, never the frontmatter root.
    """
    if explicit:
        return explicit
    if isinstance(document, Mapping):
        block = document.get("softschema")
        if isinstance(block, Mapping):
            key = block.get("envelope")
            if isinstance(key, str) and key:
                return key
    return None


def _apply(text: str, edits: Sequence[_Edit]) -> str | None:
    """Apply the edits to the source, or return None if any does not match.

    The recorded text is checked against the line before it is replaced, so a
    position that has drifted leaves the file untouched instead of corrupting it.
    """
    lines = text.splitlines(keepends=True)
    # Right to left within a line, so earlier columns stay valid.
    for edit in sorted(edits, key=lambda e: (e.line, e.col), reverse=True):
        if edit.line >= len(lines):
            return None
        line = lines[edit.line]
        if line[edit.col : edit.col + len(edit.old)] != edit.old:
            return None
        lines[edit.line] = line[: edit.col] + edit.new + line[edit.col + len(edit.old) :]
    return "".join(lines)


def conform_frontmatter_to_schema(
    path: Path, schema: Mapping[str, Any], *, envelope_key: str | None = None
) -> bool:
    """Quote the scalars *schema* calls strings in *path*'s frontmatter.

    Returns True if the file changed. A file that does not parse is left for
    ``yaml_repair``, which runs first and answers the prior question of whether
    the document is YAML at all.
    """
    if not path.exists():
        return False
    content = path.read_text()
    if not content.startswith("---\n") or "\n---\n" not in content[4:]:
        return False
    end = content.index("\n---\n", 4)
    frontmatter = content[4 : end + 1]

    yaml = _rt_yaml()
    try:
        document = yaml.load(io.StringIO(frontmatter))
    except RuamelYAMLError:
        return False
    if document is None:
        return False

    key = _envelope_key(document, envelope_key)
    payload = (
        document[key] if key and isinstance(document, Mapping) and key in document else document
    )
    edits: list[_Edit] = []
    _walk(payload, _deref(schema, schema), schema, yaml, edits)
    if not edits:
        return False

    repaired = _apply(frontmatter, edits)
    if repaired is None or repaired == frontmatter:
        return False
    path.write_text("---\n" + repaired + content[end + 1 :])
    return True


def conform_outputs_to_contracts(
    artifact_dir: Path,
    outputs: Mapping[str, Any],
    *,
    variables: Mapping[str, object] | None = None,
    registry: Any = None,
) -> list[str]:
    """Conform every frontmatter output that declares a contract.

    The contract's own pydantic model supplies the schema, so the model stays the
    single source of truth and no generated file has to be found or kept in step.
    Returns the names of the files that changed, for the operator log.

    Each output is resolved exactly as :func:`validate_item_outputs_detailed`
    resolves it -- templates rendered against ``variables``, absolute and
    multi-part paths taken as-is -- so the file this conforms is the file
    validation is about to read. Resolving by basename under ``artifact_dir``
    instead would silently skip an output whose basename carries a runtime var,
    and probe the wrong place for one living outside that directory.

    Called from the agent output path only. A code handler builds its artifact
    from typed values and a serializer already quotes those correctly; running
    this there would paper over a real bug in the handler.
    """
    if registry is None:
        from metaproc.plugins.discovery import (  # noqa: PLC0415 -- plugin discovery is
            # heavy and only needed when no registry is injected; importing it at module
            # scope would pull the plugin tree into every engine import.
            get_plugin_registry,
        )

        registry = get_plugin_registry().softschemas

    changed: list[str] = []
    for io_spec in outputs.values():
        contract_id = getattr(io_spec, "contract", None)
        declared_path = getattr(io_spec, "path", None)
        if not contract_id or not declared_path:
            continue
        if getattr(io_spec, "format", None) != "frontmatter-md":
            continue
        contract = registry.resolve(contract_id)
        model = getattr(contract, "model", None) if contract is not None else None
        if model is None:
            continue
        try:
            schema = model.model_json_schema()
        except Exception:  # noqa: BLE001 -- a schema we cannot build is one we cannot apply
            continue
        rendered = (
            resolve_templates(str(declared_path), variables) if variables else str(declared_path)
        )
        target = resolve_output_fpath(rendered, artifact_dir)
        if conform_frontmatter_to_schema(
            target, schema, envelope_key=getattr(contract, "envelope_key", None)
        ):
            changed.append(target.name)
    return changed
