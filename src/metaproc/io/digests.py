"""Streaming artifact digest helpers."""

from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


def file_sha256(path: Path) -> str:
    """Return the lowercase SHA-256 digest of ``path`` without loading it all."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_sha256(path: Path, expected: str, *, metadata_name: str) -> str:
    """Require valid SHA-256 metadata and verify it against ``path``."""
    if not expected:
        raise RuntimeError(f"{metadata_name} is required for downloaded artifact verification")
    if SHA256_PATTERN.fullmatch(expected) is None:
        raise RuntimeError(f"{metadata_name} must be exactly 64 hexadecimal characters")
    actual = file_sha256(path)
    if not hmac.compare_digest(actual, expected.lower()):
        raise RuntimeError(
            f"SHA-256 mismatch for {path.name}: expected {expected.lower()}, got {actual}"
        )
    return actual


__all__ = ["SHA256_PATTERN", "file_sha256", "verify_file_sha256"]
