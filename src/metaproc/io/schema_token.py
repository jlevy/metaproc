"""Schema token — compact identifier for self-describing frontmatter artifacts.

Token format: ``<module>:<ClassName>/<version>``

Example: ``example_plugin:RecordDocument/0.1``

The token is stored in the model's ``schema`` field (``schema_`` in Python).
Existing fields like ``form_version`` and ``retro_schema_version`` are domain
payload versions, not schema contract versions — they describe the content
format, not the envelope contract.

The namespace is the broad module name only (``metaproc`` or
``example_plugin``), not a nested sub-module path.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from metaproc.io.frontmatter import get_registered_envelopes

if TYPE_CHECKING:
    from pydantic import BaseModel

# Module must be a valid Python identifier (letters, digits, underscores).
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ClassName must be PascalCase: starts with an uppercase letter, then
# letters/digits only (no underscores).
_CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9]+$")

_TOKEN_RE = re.compile(r"^(?P<module>[^:]+):(?P<class_name>[^/]+)/(?P<version>.+)$")


class SchemaToken(NamedTuple):
    """Parsed components of a schema token."""

    module: str
    class_name: str
    version: str


def parse_schema_token(token: str) -> SchemaToken:
    """Parse a schema token string into its components.

    Raises ``ValueError`` on malformed tokens.
    """
    m = _TOKEN_RE.match(token)
    if not m:
        msg = f"Malformed schema token (expected <module>:<ClassName>/<version>): {token!r}"
        raise ValueError(msg)

    module = m.group("module")
    class_name = m.group("class_name")
    version = m.group("version")

    if not _MODULE_RE.match(module):
        msg = f"Invalid module name (must be a valid Python identifier): {module!r}"
        raise ValueError(msg)
    if not _CLASS_RE.match(class_name):
        msg = f"Invalid class name (must be PascalCase): {class_name!r}"
        raise ValueError(msg)
    if not version:
        msg = "Version must be non-empty"
        raise ValueError(msg)

    return SchemaToken(module=module, class_name=class_name, version=version)


def format_schema_token(module: str, class_name: str, version: str) -> str:
    """Produce the canonical schema token string.

    Validates components before formatting.
    """
    if not _MODULE_RE.match(module):
        msg = f"Invalid module name (must be a valid Python identifier): {module!r}"
        raise ValueError(msg)
    if not _CLASS_RE.match(class_name):
        msg = f"Invalid class name (must be PascalCase): {class_name!r}"
        raise ValueError(msg)
    if not version:
        msg = "Version must be non-empty"
        raise ValueError(msg)

    return f"{module}:{class_name}/{version}"


def _extract_schema_token(model_cls: type[BaseModel]) -> str | None:
    """Extract the schema token default from a model, if it has one.

    Looks for any field with ``alias="schema"`` whose default parses as a
    valid schema token.  This avoids hardcoding a field name.
    """
    for field_info in model_cls.model_fields.values():
        if field_info.alias != "schema":
            continue
        default = field_info.default
        if not isinstance(default, str):
            continue
        try:
            parse_schema_token(default)
        except ValueError:
            continue
        return default
    return None


def _historical_tokens(model_cls: type[BaseModel]) -> tuple[str, ...]:
    """Return any historical schema tokens this model still answers to.

    A model can declare ``historical_schema_tokens: ClassVar[tuple[str, ...]]``
    to keep older artifact tokens resolvable after a version bump, so a
    plan written under the previous schema still validates against the
    current Pydantic model. Empty tuple by default; ignored unless the
    annotation is a tuple of valid schema-token strings.
    """
    historical = getattr(model_cls, "historical_schema_tokens", None)
    if not isinstance(historical, tuple):
        return ()
    out: list[str] = []
    for entry in historical:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(entry, str):
            continue
        try:
            parse_schema_token(entry)
        except ValueError:
            continue
        out.append(entry)
    return tuple(out)


def _build_schema_registry() -> dict[str, type[BaseModel]]:
    """Build a registry mapping schema token strings to inner model classes.

    Walks registered envelopes and explicitly registered standalone models,
    extracting the schema token default from each model that declares one.
    Models that publish historical tokens via ``historical_schema_tokens``
    keep answering to those tokens after a version bump.
    """

    registry: dict[str, type[BaseModel]] = {}

    def _register(cls: type[BaseModel]) -> None:
        token = _extract_schema_token(cls)
        if token:
            registry[token] = cls
        for legacy in _historical_tokens(cls):
            registry[legacy] = cls

    for envelope_cls in get_registered_envelopes().values():
        # Check envelope-level schema token (flat envelopes)
        _register(envelope_cls)

        # Check inner model fields for nested envelopes
        for field_info in envelope_cls.model_fields.values():
            inner_type = field_info.annotation
            if inner_type is None:
                continue
            if not hasattr(inner_type, "model_fields"):
                continue
            _register(inner_type)  # pyright: ignore[reportArgumentType]

    # Standalone artifacts do not belong in the frontmatter envelope map, but their
    # canonical tokens must still resolve to their typed model.
    from metaproc.models.resources import (  # noqa: PLC0415 -- registry construction avoids model import cycles
        ResourcesDocument,
    )
    from metaproc.models.runtime import (  # noqa: PLC0415 -- registry construction avoids model import cycles
        TaskAttemptRecord,
    )

    _register(ResourcesDocument)
    _register(TaskAttemptRecord)
    return registry


def resolve_schema(token: str) -> type[BaseModel]:
    """Resolve a schema token string to its Pydantic model class.

    Raises ``KeyError`` if the token is not registered.
    """
    registry = _build_schema_registry()
    if token not in registry:
        msg = f"Unknown schema token: {token!r}. Known tokens: {sorted(registry)}"
        raise KeyError(msg)
    return registry[token]
