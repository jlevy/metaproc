"""Contracts for the uv version specifier used by the toolchain-pin guard.

The guard lets the pinned `UV_VERSION` sit anywhere inside `uv.toml`'s
`required-version` range rather than forcing it onto the floor, so the comparison has to
be a real specifier check. These cases pin the behaviour that a naive tuple compare
would get wrong.
"""

from __future__ import annotations

import pytest

from devtools.check_supply_chain import _satisfies, _version_key

UV_RANGE = ">=0.12.0,<0.13"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.12.0", True),  # on the floor
        ("0.12.3", True),  # the version this repository pins
        ("0.12.9", True),  # still inside the line, despite 9 > 3 in the last segment
        ("0.13.0", False),  # the upper bound is exclusive
        ("0.13", False),
        ("0.11.26", False),  # the previous pin no longer satisfies the range
        ("1.0.0", False),
    ],
)
def test_uv_range_admits_only_the_pinned_line(version: str, expected: bool) -> None:
    assert _satisfies(version, UV_RANGE) is expected


def test_shorter_version_is_padded_not_treated_as_lower() -> None:
    """Version 0.12 satisfies >=0.12.0; unpadded tuple comparison reports the reverse."""
    assert _satisfies("0.12", ">=0.12.0") is True


def test_bare_floor_specifier_still_supported() -> None:
    """The single-clause form this file used before the range was adopted."""
    assert _satisfies("0.11.26", ">=0.11.26") is True
    assert _satisfies("0.11.25", ">=0.11.26") is False


def test_every_clause_must_hold() -> None:
    assert _satisfies("0.12.5", ">=0.12.0,<0.13,!=0.12.5") is False


def test_unparsable_clause_fails_closed() -> None:
    """An unreadable specifier must fail the gate rather than silently pass it."""
    assert _satisfies("0.12.3", "~=0.12.0") is False


def test_version_key_ignores_prerelease_suffix() -> None:
    assert _version_key("0.12.3rc1") == (0, 12, 3)
    assert _version_key("nonsense") == ()
