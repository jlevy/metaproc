"""Tests for the built-artifact hygiene scan."""

from __future__ import annotations

import pytest

from devtools.check_distribution import _check_text_member

# A real RECORD row from a CI build: the base64 SHA-256 of METADATA happened to
# contain ``ghp_`` and twenty alphanumerics.
_CHANCE_HASH_ROW = (
    "metaproc-0.4.2.dev233+4e44f71.dist-info/METADATA,"
    # Split so this file does not itself hold the token-shaped run.
    "sha256=UnHJim1Dpx5hH8depNfghp_zQFDKQa7oz"
    "5HLMRPDrUI,18426"
)


def test_record_hash_that_spells_a_token_is_not_credential_material() -> None:
    record = f"metaproc/__init__.py,sha256=abc,10\n{_CHANCE_HASH_ROW}\nRECORD,,\n"
    _check_text_member("metaproc-0.4.2.dist-info/RECORD", record.encode())


def test_record_paths_are_still_scanned() -> None:
    token = "ghp_" + "A1" * 12
    record = f"metaproc/{token}.py,sha256=abc,10\n"
    with pytest.raises(RuntimeError, match="credential material"):
        _check_text_member("metaproc-0.4.2.dist-info/RECORD", record.encode())


def test_the_same_text_outside_record_is_still_flagged() -> None:
    with pytest.raises(RuntimeError, match="credential material"):
        _check_text_member("metaproc/notes.txt", _CHANCE_HASH_ROW.encode())
