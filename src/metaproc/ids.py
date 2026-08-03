"""Shared self-identifying immutable ID primitives.

Identifiers are `prefix-payload`, modeled on AWS resource IDs (`i-0abc123def456`). A
prefix never contains a dash, so the type is recovered by splitting exactly once.

Payloads are readable but **not parsable**: dashes inside a payload are cosmetic word
separators and no consumer may split on them. Where structure is genuinely machine-parsed,
as in the timestamped form, the interior uses `.` so the intent is explicit and cannot be
confused with a word separator.

Readers here accept a small set of alternate shapes so that already-published partitions
stay resolvable; callers never need to know which. Identity is string equality, and
nothing in this module rewrites a delimiter to make two identifiers match.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable

from strif import new_timestamped_uid, new_uid

TYPED_ID_DELIMITER = "-"
"""The delimiter between a type prefix and its payload."""

_ACCEPTED_DELIMITERS = "-_"
"""Delimiters honored on read. Only `TYPED_ID_DELIMITER` is ever written."""

_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]{0,7}$")
_PAYLOAD_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TYPED_ID_PATTERN = re.compile(
    rf"^(?P<prefix>[a-z][a-z0-9]{{0,7}})[{_ACCEPTED_DELIMITERS}](?P<payload>[A-Za-z0-9][A-Za-z0-9._-]*)$"
)
_COMPACT_RANDOM_PAYLOAD_PATTERN = re.compile(r"^[a-z0-9]{14}$")
_COMPACT_TIMESTAMPED_PAYLOAD_PATTERN = re.compile(r"^\d{8}T\d{6}Z\.\d+\.[a-z0-9]{10}$")
_HISTORICAL_TIMESTAMPED_PAYLOAD_PATTERN = re.compile(r"^\d{8}T\d{6}Z-\d+-[a-z0-9]{10}$")
_TIMESTAMPED_INTERIOR_DELIMITER = "."

_BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_BASE36_RADIX = len(_BASE36_ALPHABET)
_LEGACY_DERIVED_ID_DIGEST_BYTES = 20

DERIVED_ID_LENGTH = 14
"""Base36 characters in a derived non-temporal payload."""

TIMESTAMPED_DERIVED_ID_LENGTH = 10
"""Base36 characters in the derived component of a timestamped payload."""

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


def typed_id_pattern(prefix: str) -> str:
    """Return the anchored regex for one prefix, for reuse in schema field constraints."""
    return rf"^{re.escape(prefix)}[{_ACCEPTED_DELIMITERS}][A-Za-z0-9][A-Za-z0-9._-]*$"


def parse_typed_id(value: str) -> tuple[str, str]:
    """Split an identifier into its prefix and payload, exactly once.

    The payload is returned verbatim. It is never split further, and its delimiter is
    never rewritten, because that would change the identity.
    """
    match = _TYPED_ID_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"expected a typed ID, got {value!r}")
    return match["prefix"], match["payload"]


def is_typed_id(value: str, expected_prefix: str | None = None) -> bool:
    """Report whether `value` is a well-formed identifier of a registered type."""
    match = _TYPED_ID_PATTERN.fullmatch(value)
    if match is None or match["prefix"] not in _registered_prefixes:
        return False
    return expected_prefix is None or match["prefix"] == expected_prefix


def _require_registered_prefix(prefix: str) -> None:
    if prefix not in _registered_prefixes:
        raise ValueError(f"unregistered typed-ID prefix {prefix!r}")


def new_typed_id(
    prefix: str,
    *,
    unique_suffix: str | None = None,
    bits: int = RANDOM_TYPED_ID_BITS,
) -> str:
    """Create a typed ID with a compact non-temporal payload.

    `unique_suffix` is for a caller-owned stable namespace, such as a resumable run
    locator. The caller is responsible for uniqueness within that namespace.
    """
    _require_registered_prefix(prefix)
    payload = new_uid(bits=bits) if unique_suffix is None else unique_suffix
    if not _PAYLOAD_PATTERN.fullmatch(payload):
        raise ValueError(f"invalid payload for {prefix}{TYPED_ID_DELIMITER} ID: {payload!r}")
    return f"{prefix}{TYPED_ID_DELIMITER}{payload}"


def new_timestamped_typed_id(
    prefix: str,
    *,
    bits: int = TIMESTAMPED_TYPED_ID_RANDOM_BITS,
) -> str:
    """Create a typed identity for an event whose allocation time is meaningful."""
    _require_registered_prefix(prefix)
    payload = new_timestamped_uid(bits=bits).replace("-", _TIMESTAMPED_INTERIOR_DELIMITER)
    return f"{prefix}{TYPED_ID_DELIMITER}{payload}"


def _encode_base36(value: int, *, length: int) -> str:
    encoded: list[str] = []
    while value:
        value, remainder = divmod(value, _BASE36_RADIX)
        encoded.append(_BASE36_ALPHABET[remainder])
    return "".join(reversed(encoded or ["0"])).rjust(length, "0")


def _timestamped_payload_parts(payload: str) -> tuple[str, str] | None:
    """Split a timestamped payload into its timestamp and derived halves.

    This is the one place structure is read out of a payload, which is why the current
    form uses `.`. The historical dash form is still recognized so an existing parent can
    seed a child.
    """
    if _COMPACT_TIMESTAMPED_PAYLOAD_PATTERN.fullmatch(payload):
        timestamp, _, random_part = payload.rpartition(_TIMESTAMPED_INTERIOR_DELIMITER)
        return timestamp, random_part
    if _HISTORICAL_TIMESTAMPED_PAYLOAD_PATTERN.fullmatch(payload):
        timestamp, _, random_part = payload.rpartition("-")
        return timestamp.replace("-", _TIMESTAMPED_INTERIOR_DELIMITER), random_part
    return None


def _uses_compact_derivation(parent_id: str) -> bool:
    _, payload = parse_typed_id(parent_id)
    return bool(
        _COMPACT_RANDOM_PAYLOAD_PATTERN.fullmatch(payload)
        or _timestamped_payload_parts(payload) is not None
    )


def derive_typed_id_from_key(
    prefix: str,
    stable_key: str,
    *,
    length: int = DERIVED_ID_LENGTH,
) -> str:
    """Derive a compact non-temporal identity from a stable caller-owned key."""
    _require_registered_prefix(prefix)
    if not stable_key:
        raise ValueError("stable_key must be nonempty")
    digest = hashlib.sha256(stable_key.encode()).digest()
    namespace_size = _BASE36_RADIX**length
    payload = _encode_base36(int.from_bytes(digest, "big") % namespace_size, length=length)
    return f"{prefix}{TYPED_ID_DELIMITER}{payload}"


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
    payload = (
        base64.b32encode(digest[:_LEGACY_DERIVED_ID_DIGEST_BYTES]).decode().lower().rstrip("=")
    )
    return f"{prefix}{TYPED_ID_DELIMITER}{payload}"


def derive_timestamped_typed_child_id(
    prefix: str,
    parent_id: str,
    stable_key: str,
) -> str:
    """Derive a replay-stable compact timestamped identity within a run namespace."""
    _require_registered_prefix(prefix)
    _require_any_typed_id(parent_id)
    _, parent_payload = parse_typed_id(parent_id)
    parts = _timestamped_payload_parts(parent_payload)
    if parts is None:
        raise ValueError(f"expected a compact timestamped parent ID, got {parent_id!r}")
    if not stable_key:
        raise ValueError("stable_key must be nonempty")
    timestamp, _random = parts
    digest = hashlib.sha256(f"{parent_id}\x1f{stable_key}".encode()).digest()
    namespace_size = _BASE36_RADIX**TIMESTAMPED_DERIVED_ID_LENGTH
    derived = _encode_base36(
        int.from_bytes(digest, "big") % namespace_size,
        length=TIMESTAMPED_DERIVED_ID_LENGTH,
    )
    return f"{prefix}{TYPED_ID_DELIMITER}{timestamp}{_TIMESTAMPED_INTERIOR_DELIMITER}{derived}"


def _require_any_typed_id(value: str) -> str:
    if not is_typed_id(value):
        raise ValueError(f"expected a registered typed ID, got {value!r}")
    return value


def require_typed_id(value: str, expected_prefix: str) -> str:
    """Return `value` when it is a valid ID of the expected type, else fail."""
    _require_registered_prefix(expected_prefix)
    if not is_typed_id(value, expected_prefix):
        raise ValueError(f"expected {expected_prefix}{TYPED_ID_DELIMITER} ID, got {value!r}")
    return value


def require_compact_timestamped_typed_id(value: str, expected_prefix: str) -> str:
    """Validate the current timestamped allocation form for a new run namespace."""
    require_typed_id(value, expected_prefix)
    _, payload = parse_typed_id(value)
    if _timestamped_payload_parts(payload) is None:
        raise ValueError(
            f"expected a compact timestamped {expected_prefix}{TYPED_ID_DELIMITER} ID, got {value!r}"
        )
    return value
