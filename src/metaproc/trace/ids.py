"""Deterministic ``span_id`` generation.

Span IDs are SHA-1 of ``(source, *unique_key_parts)`` truncated to 16 hex
chars. Determinism means re-extracting the same logs produces the same
IDs, which makes cross-source parent reconciliation in the linker idempotent
and lets the CLI cache extracted traces safely.

16 hex chars = 64 bits of namespace = ~10^19 distinct values. Per
``trace_id`` we expect at most ~10^4 spans (a typical large cohort run
produces O(thousands) of spans — agent attempts × tool calls each).
Birthday-collision probability at 10^4 against 10^19 is negligible
(~5e-12).
"""

from __future__ import annotations

import hashlib

_SPAN_ID_HEX_LEN = 16


def compute_span_id(source: str, *unique_key_parts: object) -> str:
    """Deterministic ``span_id`` from ``(source, *unique_key_parts)``.

    Each part is stringified via ``str(...)`` then joined with ``\\x1f``
    (unit separator) so two parts that stringify the same can't collide
    with one merged part. Source-specific extractors choose their own
    ``unique_key_parts``; see plan § Identifier conventions for the
    convention per source.
    """
    if not source:
        msg = "compute_span_id: source must be non-empty"
        raise ValueError(msg)
    sep = "\x1f"
    payload = sep.join([source, *(str(p) for p in unique_key_parts)])
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()  # noqa: S324 — IDs, not crypto
    return digest[:_SPAN_ID_HEX_LEN]
