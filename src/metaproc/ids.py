"""Shared self-identifying immutable ID primitives."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable

from strif import new_timestamped_uid, new_uid

_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]{0,7}$")
_SUFFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_COMPACT_RANDOM_SUFFIX_PATTERN = re.compile(r"^[a-z0-9]{14}$")
_COMPACT_TIMESTAMPED_SUFFIX_PATTERN = re.compile(r"^\d{8}T\d{6}Z-\d+-[a-z0-9]{10}$")
_BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_BASE36_RADIX = len(_BASE36_ALPHABET)
_DERIVED_ID_LENGTH = 14
_TIMESTAMPED_DERIVED_ID_LENGTH = 10
_LEGACY_DERIVED_ID_DIGEST_BYTES = 20

RANDOM_TYPED_ID_BITS = 72
"""Randomness requested for non-temporal typed identities."""

TIMESTAMPED_TYPED_ID_RANDOM_BITS = 48
"""Randomness requested in addition to a timestamp for time-bound identities."""

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
    """Create a typed ID with a compact non-temporal suffix.

    `unique_suffix` is for a caller-owned stable namespace, such as a resumable run
    locator. The caller is responsible for uniqueness within that namespace.
    """
    _require_registered_prefix(prefix)
    suffix = new_uid(bits=RANDOM_TYPED_ID_BITS) if unique_suffix is None else unique_suffix
    if not _SUFFIX_PATTERN.fullmatch(suffix):
        raise ValueError(f"invalid suffix for {prefix}_ ID: {suffix!r}")
    return f"{prefix}_{suffix}"


def new_timestamped_typed_id(prefix: str) -> str:
    """Create a typed identity for an event whose allocation time is meaningful."""
    _require_registered_prefix(prefix)
    return f"{prefix}_{new_timestamped_uid(bits=TIMESTAMPED_TYPED_ID_RANDOM_BITS)}"


def _encode_base36(value: int, *, length: int) -> str:
    encoded: list[str] = []
    while value:
        value, remainder = divmod(value, _BASE36_RADIX)
        encoded.append(_BASE36_ALPHABET[remainder])
    return "".join(reversed(encoded or ["0"])).rjust(length, "0")


def _uses_compact_derivation(parent_id: str) -> bool:
    suffix = parent_id.partition("_")[2]
    return bool(
        _COMPACT_RANDOM_SUFFIX_PATTERN.fullmatch(suffix)
        or _COMPACT_TIMESTAMPED_SUFFIX_PATTERN.fullmatch(suffix)
    )


def derive_typed_id_from_key(prefix: str, stable_key: str) -> str:
    """Derive a compact non-temporal identity from a stable caller-owned key."""
    _require_registered_prefix(prefix)
    if not stable_key:
        raise ValueError("stable_key must be nonempty")
    digest = hashlib.sha256(stable_key.encode()).digest()
    namespace_size = _BASE36_RADIX**_DERIVED_ID_LENGTH
    suffix = _encode_base36(
        int.from_bytes(digest, "big") % namespace_size,
        length=_DERIVED_ID_LENGTH,
    )
    return f"{prefix}_{suffix}"


def derive_typed_child_id(prefix: str, parent_id: str, ordinal: int) -> str:
    """Derive a stable child identity from an immutable parent and ordinal."""
    _require_registered_prefix(prefix)
    if isinstance(ordinal, bool) or ordinal < 0:
        raise ValueError(f"ordinal must be non-negative, got {ordinal!r}")
    _require_any_typed_id(parent_id)
    identity = f"{parent_id}\x1f{ordinal}"
    if _uses_compact_derivation(parent_id):
        return derive_typed_id_from_key(prefix, identity)
    digest = hashlib.sha256(identity.encode()).digest()
    suffix = base64.b32encode(digest[:_LEGACY_DERIVED_ID_DIGEST_BYTES]).decode().lower().rstrip("=")
    return f"{prefix}_{suffix}"


def derive_timestamped_typed_child_id(
    prefix: str,
    parent_id: str,
    stable_key: str,
) -> str:
    """Derive a replay-stable compact timestamped identity within a run namespace."""
    _require_registered_prefix(prefix)
    _require_any_typed_id(parent_id)
    parent_suffix = parent_id.partition("_")[2]
    if not _COMPACT_TIMESTAMPED_SUFFIX_PATTERN.fullmatch(parent_suffix):
        raise ValueError(f"expected a compact timestamped parent ID, got {parent_id!r}")
    if not stable_key:
        raise ValueError("stable_key must be nonempty")
    timestamp, _separator, _random = parent_suffix.rpartition("-")
    digest = hashlib.sha256(f"{parent_id}\x1f{stable_key}".encode()).digest()
    namespace_size = _BASE36_RADIX**_TIMESTAMPED_DERIVED_ID_LENGTH
    derived = _encode_base36(
        int.from_bytes(digest, "big") % namespace_size,
        length=_TIMESTAMPED_DERIVED_ID_LENGTH,
    )
    return f"{prefix}_{timestamp}-{derived}"


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


def require_compact_timestamped_typed_id(value: str, expected_prefix: str) -> str:
    """Validate the current timestamped allocation form for a new run namespace."""
    require_typed_id(value, expected_prefix)
    suffix = value.removeprefix(f"{expected_prefix}_")
    if not _COMPACT_TIMESTAMPED_SUFFIX_PATTERN.fullmatch(suffix):
        raise ValueError(f"expected a compact timestamped {expected_prefix}_ ID, got {value!r}")
    return value
