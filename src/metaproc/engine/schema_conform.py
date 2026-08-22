"""Make an agent-authored document say the types its contract asks for.

YAML plain scalars carry no type marker: ``1850`` is an integer because of how
the characters look, not because anyone said so. A program serializing a known
value never hits this, because every YAML emitter quotes a string that would
otherwise resolve to something else. An agent writing the document by hand has
no serializer in the path, so a brand genuinely named ``1850`` arrives as an
integer and fails a ``type: string`` contract.

This module puts the missing serializer back, and it borrows both halves rather
than reimplementing either:

- **The contract's own model decides what is wrong.** The document is validated
  with the same pydantic model that will judge it seconds later, and the only
  errors acted on are ``string_type`` — pydantic's way of saying "a string
  belongs here and something else arrived". Unions that already accept the
  value, explicit nulls, missing fields, shape mismatches and genuinely wrong
  values are all reported as other error types, so they pass through untouched
  and still fail. There is no second opinion about the schema kept here to drift
  from the first.

- **The document's own serializer decides how to write it.** The frontmatter is
  loaded round-trip, the offending scalars are replaced with their own source
  text as strings, and the document is written back through the serializer
  everything else here writes with. The emitter, not this module, decides
  quoting: handing it ``"1850"`` is what makes it write ``'1850'``.

The result is one direction only — a scalar becomes a string where the contract
asks for one, and nothing else changes — and it is lossless, because the text
comes from the scalar as written (``1.10`` stays ``1.10``, ``007`` stays
``007``) rather than from ``str()`` of the parsed value.

The pass runs on freshly emitted agent artifacts, after ``yaml_repair`` (which
answers the prior question of whether the document is YAML at all) and before
validation. The file is seconds old and unread, so re-emitting it is free.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, MutableMapping, Sequence
from io import StringIO
from pathlib import Path
from typing import Any

from frontmatter_format import fmf_split_frontmatter, new_yaml
from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from strif import atomic_output_file

from metaproc.engine.placeholders import resolve_templates
from metaproc.engine.validation import _resolve_output_fpath

# Pydantic's verdict for "a string belongs here and something else arrived".
# The one error this pass acts on; every other error is a real disagreement.
_STRING_TYPE_ERROR = "string_type"

# Scalar types a YAML document hands back that have a faithful written form.
# ``None`` is absent deliberately: a null is an absent value, not a notation
# accident, and pydantic reports it as ``string_type`` too when a plain ``str``
# field receives it — stringifying that would invent data.
_COERCIBLE = (bool, int, float, datetime.date, datetime.time, datetime.datetime)

_MD_DELIMITER = "---\n"

# A defect can hide another: fixing a parent can reveal a child pydantic never
# reached. Two extra passes is far more than the nesting our contracts have.
_MAX_ROUNDS = 3


def _rt_yaml() -> YAML:
    """The document serializer: the house ``new_yaml`` in round-trip mode.

    ``suppress_vals`` is off because this pass must never drop a value the author
    wrote, explicit nulls included.
    """
    yaml = new_yaml(typ="rt", suppress_vals=lambda _value: False)
    yaml.preserve_quotes = True
    yaml.width = 4096  # never re-wrap an author's long line
    # Match the indentation the artifact templates use, so a document this pass
    # barely touches comes back byte-identical apart from the scalars it fixed.
    # Restyling is not this pass's job: it would bury a one-scalar correction in
    # a whole-file diff, on artifacts that are published and reviewed.
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _scalar_text(yaml: YAML, value: object) -> str:
    """The text the serializer itself would emit for this scalar.

    Round-trip scalar types remember their notation and the representer is the
    component that reconstructs it, so ask it for the node: ``1.10`` comes back
    as ``"1.10"``, ``007`` as ``"007"``, ``0x1F`` as ``"0x1F"``. ``str()``
    collapses all three.
    """
    return str(yaml.representer.represent_data(value).value)


def _node_at(root: Any, loc: Sequence[Any]) -> tuple[Any, Any] | None:
    """The container and key holding the value at *loc*, or None if unreachable.

    ``loc`` is pydantic's error location: a path of mapping keys and sequence
    indices. Anything that does not resolve means no edit, never a wrong one.
    """
    if not loc:
        return None
    node = root
    for step in loc[:-1]:
        if isinstance(node, MutableMapping) and step in node:
            node = node[step]
        elif isinstance(node, list) and isinstance(step, int) and 0 <= step < len(node):
            node = node[step]
        else:
            return None
    key = loc[-1]
    if isinstance(node, MutableMapping) and key in node:
        return node, key
    if isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
        return node, key
    return None


def _string_type_locations(model: type[BaseModel], payload: object) -> list[Sequence[Any]]:
    """Where the contract wanted a string and got something else.

    Everything pydantic reports under any other error type is a real
    disagreement with the contract and is left alone to fail.
    """
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        return [err["loc"] for err in exc.errors() if err["type"] == _STRING_TYPE_ERROR]
    return []


def _conform_payload(model: type[BaseModel], payload: object, yaml: YAML) -> bool:
    """Replace mistyped scalars with their own text. True if anything changed."""
    changed = False
    for _round in range(_MAX_ROUNDS):
        locations = _string_type_locations(model, payload)
        if not locations:
            break
        fixed_this_round = False
        for loc in locations:
            found = _node_at(payload, loc)
            if found is None:
                continue
            container, key = found
            value = container[key]
            if not isinstance(value, _COERCIBLE) or isinstance(value, str):
                continue
            # A plain str: the emitter quotes it if its text would otherwise
            # resolve as something else, which is the serializer doing the one
            # job this module exists to reinstate.
            container[key] = _scalar_text(yaml, value)
            changed = True
            fixed_this_round = True
        if not fixed_this_round:
            break
    return changed


def _envelope_key(document: Any, explicit: str | None) -> str | None:
    """The key the payload lives under, from the caller or the document itself.

    A softschema artifact names its own envelope in its ``softschema`` block, so
    a standalone caller does not need the registry to find the payload. The
    model describes the envelope's contents, never the frontmatter root.
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


def conform_frontmatter_to_model(
    path: Path, model: type[BaseModel], *, envelope_key: str | None = None
) -> bool:
    """Re-emit *path*'s frontmatter with its scalars conformed to *model*.

    Returns True if the file changed. A document that does not parse is left for
    ``yaml_repair``, which runs first and owns that question.
    """
    if not path.exists():
        return False
    content = path.read_text()
    if not content.startswith(_MD_DELIMITER):
        return False
    metadata_str, body_offset, _meta_start = fmf_split_frontmatter(content, strict=False)
    if metadata_str is None:
        return False

    yaml = _rt_yaml()
    try:
        document = yaml.load(metadata_str)
    except YAMLError:
        return False
    if document is None:
        return False

    key = _envelope_key(document, envelope_key)
    payload = (
        document[key] if key and isinstance(document, Mapping) and key in document else document
    )
    if not _conform_payload(model, payload, yaml):
        return False

    buf = StringIO()
    yaml.dump(document, buf)
    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(_MD_DELIMITER + buf.getvalue() + _MD_DELIMITER + content[body_offset:])
    return True


def conform_declared_outputs(
    item_dir: Path,
    outputs: Mapping[str, Any],
    *,
    variables: Mapping[str, object] | None = None,
    registry: Any = None,
) -> list[Path]:
    """Conform declared frontmatter outputs to the contracts they name.

    Resolves each output path exactly as ``repair_declared_outputs`` and
    ``validate_item_outputs_detailed`` do, so the file conformed is the file
    validation is about to read.

    Returns the paths actually changed, for progress reporting.

    Scoped to agent-authored outputs, like the repair pass it follows. A code
    handler builds its artifact from typed values through a real writer, and
    every YAML emitter already quotes a string that would otherwise resolve to
    something else; running this there would paper over a genuine bug in the
    handler.
    """
    if registry is None:
        # Deferred: plugin discovery imports the engine, so a module-level
        # import here would cycle.
        from metaproc.plugins.discovery import (  # noqa: PLC0415 -- circular-import guard
            get_plugin_registry,
        )

        registry = get_plugin_registry().softschemas

    changed: list[Path] = []
    for io_spec in outputs.values():
        contract_id = getattr(io_spec, "contract", None)
        declared = getattr(io_spec, "path", None)
        if not contract_id or not declared or getattr(io_spec, "kind", None) == "directory":
            continue
        if getattr(io_spec, "format", None) != "frontmatter-md":
            continue
        contract = registry.resolve(contract_id)
        model = getattr(contract, "model", None) if contract is not None else None
        if model is None or not isinstance(model, type) or not issubclass(model, BaseModel):
            continue
        rendered = resolve_templates(str(declared), variables) if variables else str(declared)
        fpath = _resolve_output_fpath(rendered, item_dir)
        if not fpath.is_file():
            continue
        if conform_frontmatter_to_model(
            fpath, model, envelope_key=getattr(contract, "envelope_key", None)
        ):
            changed.append(fpath)
    return changed
