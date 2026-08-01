"""Behavioral tests for self-identifying immutable IDs."""

from __future__ import annotations

import re

import pytest

from metaproc.ids import derive_typed_child_id, new_typed_id, require_typed_id


def test_new_ids_are_typed_sortable_and_collision_resistant() -> None:
    identifiers = [new_typed_id("art") for _ in range(128)]

    assert len(set(identifiers)) == len(identifiers)
    assert all(re.fullmatch(r"art_\d{8}T\d{6}Z-\d+-[a-z0-9]+", value) for value in identifiers)


def test_child_ids_are_deterministic_within_parent_ordinal_namespace() -> None:
    parent = "art_20260801T120000Z-000001-parent"

    first = derive_typed_child_id("rev", parent, 0)

    assert first == derive_typed_child_id("rev", parent, 0)
    assert first != derive_typed_child_id("rev", parent, 1)
    assert first != derive_typed_child_id("rev", "art_20260801T120000Z-000002-parent", 0)
    assert re.fullmatch(r"rev_[a-z0-9]+", first)


def test_validation_names_the_expected_type_and_offending_value() -> None:
    with pytest.raises(ValueError, match=r"expected run_ ID, got 'art_wrong'"):
        require_typed_id("art_wrong", "run")

    with pytest.raises(ValueError, match=r"unregistered typed-ID prefix 'unknown'"):
        new_typed_id("unknown")

    with pytest.raises(ValueError, match=r"invalid suffix for run_ ID"):
        new_typed_id("run", unique_suffix="")

    with pytest.raises(ValueError, match=r"ordinal must be non-negative"):
        derive_typed_child_id("rev", "art_parent", -1)
