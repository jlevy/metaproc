"""Shared self-identifying immutable ID primitives."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable

from strif import new_timestamped_uid

_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]{0,7}$")
_SUFFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TIMESTAMPED_UID_RANDOM_BITS = 96
_DERIVED_ID_DIGEST_BYTES = 20

METAPROC_ID_PREFIXES = frozenset({"run", "art", "rev", "evt", "use", "bud", "rcpt"})
_registered_prefixes = set(METAPROC_ID_PREFIXES)


def register_typed_id_prefixes(prefixes: Iterable[str]) -> None:
    """Register stable ID prefixes owned by an importing domain package."""
    for prefix in prefixes:
        if not _PREFIX_PATTERN.fullmatch(prefix):
            raise ValueError(
                f"invalid typed-ID prefix {prefix!r}; expected 1-8 lowercase alphanumeric characters"
            )
        _registered_prefixes.add(prefix)


def _require_registered_prefix(prefix: str) -> None:
    if prefix not in _registered_prefixes:
        raise ValueError(f"unregistered typed-ID prefix {prefix!r}")


def new_typed_id(prefix: str, *, unique_suffix: str | None = None) -> str:
    """Create a typed ID with a sortable high-entropy suffix.

    `unique_suffix` is for a caller-owned stable namespace, such as a resumable run
    locator. The caller is responsible for uniqueness within that namespace.
    """
    _require_registered_prefix(prefix)
    suffix = (
        new_timestamped_uid(bits=_TIMESTAMPED_UID_RANDOM_BITS)
        if unique_suffix is None
        else unique_suffix
    )
    if not _SUFFIX_PATTERN.fullmatch(suffix):
        raise ValueError(f"invalid suffix for {prefix}_ ID: {suffix!r}")
    return f"{prefix}_{suffix}"


def derive_typed_child_id(prefix: str, parent_id: str, ordinal: int) -> str:
    """Derive a stable child identity from an immutable parent and ordinal."""
    _require_registered_prefix(prefix)
    if isinstance(ordinal, bool) or ordinal < 0:
        raise ValueError(f"ordinal must be non-negative, got {ordinal!r}")
    _require_any_typed_id(parent_id)
    digest = hashlib.sha256(f"{parent_id}\x1f{ordinal}".encode()).digest()
    suffix = base64.b32encode(digest[:_DERIVED_ID_DIGEST_BYTES]).decode().lower().rstrip("=")
    return f"{prefix}_{suffix}"


def _require_any_typed_id(value: str) -> str:
    prefix, separator, suffix = value.partition("_")
    if not separator or prefix not in _registered_prefixes or not _SUFFIX_PATTERN.fullmatch(suffix):
        raise ValueError(f"expected a registered typed ID, got {value!r}")
    return value


def require_typed_id(value: str, expected_prefix: str) -> str:
    """Return `value` when it is a valid ID of the expected type, else fail."""
    _require_registered_prefix(expected_prefix)
    expected_start = f"{expected_prefix}_"
    suffix = value.removeprefix(expected_start) if value.startswith(expected_start) else ""
    if not suffix or not _SUFFIX_PATTERN.fullmatch(suffix):
        raise ValueError(f"expected {expected_start} ID, got {value!r}")
    return value
