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

Two notations do not survive being retyped, both recorded in
``TestRecordedNotationLimits``: a boolean comes back canonically spelled
(``True`` becomes ``'true'``), and an integer's leading ``+`` is dropped
(``+1_000`` becomes ``'1_000'``). Round-trip mode remembers a scalar's notation
in the value it hands back, and neither of those two is part of what it
remembers. Recovering either means re-reading the source text by hand, which is
the string surgery this module exists to avoid, and neither is reachable for a
name a person would give something.

The pass runs on freshly emitted agent artifacts, after ``yaml_repair`` (which
answers the prior question of whether the document is YAML at all) and before
validation. The file is seconds old and unread, so re-emitting it is free.
Both passes are scoped to the same call sites for the same reason; see the
``engine.yaml_repair`` module docstring before adding one.

A second normalization layer answers a related question elsewhere:
``validation.normalize_for_structural_pass`` converts dates, decimals and UUIDs
to their serialized form *at read time*, so validation judges the document the
schema describes. That makes a run fair. This pass writes the correction to
disk instead, because the artifact is published and its readers — a downstream
consumer's own contract checker among them — do not run metaproc's normalizer.
The two overlap on dates by design: the layers answer "is this run fair?" and
"is this artifact right?", and only the second one outlives the run.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Mapping, MutableMapping, Sequence
from io import StringIO
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from softschema import Contracts

from metaproc.engine.placeholders import resolve_templates
from metaproc.engine.validation import resolve_output_fpath
from metaproc.io import atomic_output_file, fmf_split_frontmatter, new_yaml
from metaproc.models.authored import IOSpec

# Pydantic's verdict for "a string belongs here and something else arrived".
# The one error this pass acts on; every other error is a real disagreement.
log = logging.getLogger(__name__)

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


class _FromDocument:
    """Sentinel for ``envelope_key``: read the envelope name from the document.

    Distinct from ``None``, which means the caller knows there is no envelope and
    the model describes the frontmatter root. A caller holding the contract knows
    which of the two applies and must not fall back to the document's own claim.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "FROM_DOCUMENT"


FROM_DOCUMENT: Final = _FromDocument()


def _rt_yaml() -> YAML:
    """The document serializer: the house ``new_yaml`` in round-trip mode.

    ``suppress_vals`` is off because this pass must never drop a value the author
    wrote, explicit nulls included. ``allow_aliases`` is on because ``new_yaml``
    otherwise makes the representer ignore them, which expands an author's
    ``*ref`` into a duplicated mapping — a structural rewrite, and the one thing
    round-trip mode is here to prevent.
    """
    yaml = new_yaml(typ="rt", suppress_vals=lambda _value: False, allow_aliases=True)
    # ruamel's round-trip representer writes a null as an empty value once any
    # object has been represented, and as ``null`` before that. Disabling aliases
    # used to keep that bookkeeping permanently empty, so every null came out
    # ``null`` by accident; enabling them makes the spelling depend on position.
    # Pin it, because a null this pass did not touch changing shape is the same
    # unasked-for diff the anchors were.
    yaml.representer.add_representer(
        type(None),
        lambda dumper, _data: dumper.represent_scalar("tag:yaml.org,2002:null", "null"),
    )
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
    representer = yaml.representer
    node = representer.represent_data(value)
    # ``represent_data`` is normally reached through ``represent``, which clears
    # this bookkeeping when the document is done. Reaching it directly does not,
    # so a value the pass merely inspected would still be registered as
    # already-represented when the real dump runs, and could be emitted as an
    # alias to a node no longer in the tree.
    representer.represented_objects.clear()
    representer.object_keeper.clear()
    representer.alias_key = None
    return str(node.value)


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
    except Exception as e:  # noqa: BLE001 -- a model we cannot run is one we cannot apply
        # A contract model is downstream code: a validator of its own can raise
        # anything. This pass is an optimization on the way to validation, which
        # runs next and owns reporting the failure, so a model that will not run
        # turns the pass off for that artifact rather than taking the step down.
        log.debug("cannot validate against %s, skipping conform: %s", model.__name__, e)
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
            if not isinstance(value, _COERCIBLE):
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


def _payload_for(document: Any, path: Path, envelope_key: str | None | _FromDocument) -> Any:
    """The part of the document the model describes, or None if it is absent.

    A caller holding the contract states the envelope, and a missing one is then
    a real disagreement for validation to report — not licence to apply the
    envelope's model to the frontmatter root, which is a different level
    describing different fields. Only a standalone caller, with no contract in
    hand, reads the envelope from the artifact's own ``softschema`` block.
    """
    if isinstance(envelope_key, _FromDocument):
        key = None
        if isinstance(document, Mapping):
            block = document.get("softschema")
            if isinstance(block, Mapping):
                declared = block.get("envelope")
                key = declared if isinstance(declared, str) and declared else None
    else:
        key = envelope_key
    if key is None:
        return document
    if not isinstance(document, Mapping) or key not in document:
        log.debug("%s: contract expects envelope '%s', which the document omits", path, key)
        return None
    return document[key]


def conform_frontmatter_to_model(
    path: Path,
    model: type[BaseModel],
    *,
    envelope_key: str | None | _FromDocument = FROM_DOCUMENT,
) -> bool:
    """Re-emit *path*'s frontmatter with its scalars conformed to *model*.

    Returns True if the file changed. A document that does not parse is left for
    ``yaml_repair``, which runs first and owns that question.

    ``envelope_key`` is the key the payload lives under: a string names it,
    ``None`` says the model describes the frontmatter root, and the default
    ``FROM_DOCUMENT`` reads it from the artifact's own ``softschema`` block.
    """
    if not path.exists():
        return False
    # ``newline=""`` keeps the file's own line endings out of the read, so a CRLF
    # document is not silently reflowed to LF by the write-back.
    with path.open(encoding="utf-8", newline="") as handle:
        content = handle.read()
    crlf = "\r\n" in content
    if crlf:
        content = content.replace("\r\n", "\n")
    if not content.startswith(_MD_DELIMITER):
        # Frontmatter-format also writes an HTML-comment style (``<!---``). This
        # pass reads the one style ``format: frontmatter-md`` emits; anything
        # else is out of scope rather than clean.
        log.debug("%s: no '---' frontmatter, not a document this pass reads", path)
        return False
    metadata_str, body_offset, _meta_start = fmf_split_frontmatter(content, strict=False)
    if metadata_str is None:
        return False

    yaml = _rt_yaml()
    try:
        document = yaml.load(metadata_str)
    except YAMLError as e:
        log.debug("%s: frontmatter does not parse, leaving it to yaml_repair (%s)", path, e)
        return False
    if document is None:
        return False

    payload = _payload_for(document, path, envelope_key)
    if payload is None or not _conform_payload(model, payload, yaml):
        return False

    buf = StringIO()
    yaml.dump(document, buf)
    text = _MD_DELIMITER + buf.getvalue() + _MD_DELIMITER + content[body_offset:]
    if crlf:
        text = text.replace("\n", "\r\n")
    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(text, encoding="utf-8", newline="")
    return True


def conform_declared_outputs(
    item_dir: Path,
    outputs: Mapping[str, IOSpec],
    *,
    variables: Mapping[str, object] | None = None,
    registry: Contracts | None = None,
) -> list[Path]:
    """Conform declared frontmatter outputs to the contracts they name.

    Resolves each output path through the shared ``resolve_output_fpath``, the
    same call ``repair_declared_outputs`` and ``validate_item_outputs_detailed``
    make, so the file conformed is the file validation is about to read. Path
    resolution is shared; existence probing is not, and deliberately so — both
    rewriting passes read and write plain text, so neither follows validation's
    ``.gz`` sibling, and a compressed artifact is validated without being
    conformed.

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
        contract_id = io_spec.contract
        declared = io_spec.path
        if not contract_id or not declared or io_spec.kind == "directory":
            continue
        if io_spec.format != "frontmatter-md":
            continue
        contract = registry.resolve(contract_id)
        if contract is None:
            continue
        model = contract.model
        if model is None:
            # A contract can carry a generated schema without a model; there is
            # nothing for this pass to validate against, and validation says so.
            log.debug("contract '%s' has no pydantic model to conform against", contract_id)
            continue
        rendered = resolve_templates(str(declared), variables) if variables else str(declared)
        fpath = resolve_output_fpath(rendered, item_dir)
        if not fpath.is_file():
            continue
        if conform_frontmatter_to_model(fpath, model, envelope_key=contract.envelope_key):
            changed.append(fpath)
    return changed
