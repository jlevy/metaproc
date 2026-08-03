"""Behavioral tests for self-identifying immutable IDs."""

from __future__ import annotations

import re

import pytest

from metaproc.ids import (
    derive_timestamped_typed_child_id,
    derive_typed_child_id,
    derive_typed_id_from_key,
    is_typed_id,
    new_timestamped_typed_id,
    new_typed_id,
    parse_typed_id,
    require_typed_id,
)


def test_new_non_temporal_ids_are_compact_base36_and_collision_resistant() -> None:
    identifiers = [new_typed_id("art") for _ in range(128)]

    assert len(set(identifiers)) == len(identifiers)
    assert all(re.fullmatch(r"art-[a-z0-9]{14}", value) for value in identifiers)


def test_new_timestamped_ids_keep_strif_run_ordering_with_compact_randomness() -> None:
    identifiers = [new_timestamped_typed_id("run") for _ in range(128)]

    assert len(set(identifiers)) == len(identifiers)
    assert all(re.fullmatch(r"run-\d{8}T\d{6}Z\.\d+\.[a-z0-9]{10}", value) for value in identifiers)


def test_child_ids_are_deterministic_within_parent_ordinal_namespace() -> None:
    parent = "art_abc123def45678"

    first = derive_typed_child_id("rev", parent, 0)

    assert first == derive_typed_child_id("rev", parent, 0)
    assert first != derive_typed_child_id("rev", parent, 1)
    assert first != derive_typed_child_id("rev", "art_123456defabc78", 0)
    assert re.fullmatch(r"rev-[a-z0-9]{14}", first)


def test_stable_key_ids_are_compact_and_deterministic() -> None:
    first = derive_typed_id_from_key("use", "run_legacy-readable-id")

    assert first == derive_typed_id_from_key("use", "run_legacy-readable-id")
    assert first != derive_typed_id_from_key("use", "run_other-readable-id")
    assert re.fullmatch(r"use-[a-z0-9]{14}", first)


def test_legacy_parent_replay_preserves_the_existing_derived_identity() -> None:
    parent = "art_20260801T120000Z-000001-parent"

    assert derive_typed_child_id("rev", parent, 0) == "rev-fktwhn7txlin26pimnbi4sk4oyyd5gz7"


def test_timestamped_child_keeps_parent_time_and_derives_compact_uniqueness() -> None:
    parent = "run_20260802T193000Z-1234560000-abc123def4"

    first = derive_timestamped_typed_child_id("run", parent, "RCKY\x1f2026-07-31")

    assert first == derive_timestamped_typed_child_id("run", parent, "RCKY\x1f2026-07-31")
    assert first != derive_timestamped_typed_child_id("run", parent, "GIL\x1f2026-07-31")
    assert re.fullmatch(r"run-20260802T193000Z\.1234560000\.[a-z0-9]{10}", first)


def test_validation_accepts_legacy_current_and_future_suffix_lengths() -> None:
    values = (
        "rev_xpqtcvybhgvjdl5ol42p7dhfio5zoc3d",
        "rev_abc123def45678",
        "rev_z",
        "rev_future-format.with-extra_context-1",
    )

    assert tuple(require_typed_id(value, "rev") for value in values) == values


def test_validation_names_the_expected_type_and_offending_value() -> None:
    with pytest.raises(ValueError, match=r"expected run- ID, got 'art_wrong'"):
        require_typed_id("art_wrong", "run")

    with pytest.raises(ValueError, match=r"unregistered typed-ID prefix 'unknown'"):
        new_typed_id("unknown")

    with pytest.raises(ValueError, match=r"invalid payload for run- ID"):
        new_typed_id("run", unique_suffix="")

    with pytest.raises(ValueError, match=r"ordinal must be non-negative"):
        derive_typed_child_id("rev", "art_parent", -1)


def test_dash_and_underscore_parse_same_payload_but_are_different_identities() -> None:
    dash_prefix, dash_payload = parse_typed_id("run-abc")
    under_prefix, under_payload = parse_typed_id("run_abc")

    assert dash_prefix == under_prefix == "run"
    assert dash_payload == under_payload == "abc"
    assert "run-abc" != "run_abc"


def test_historical_underscore_identifier_passes_validation() -> None:
    assert is_typed_id("run_legacy123abc45")
    assert is_typed_id("run_legacy123abc45", "run")
    assert require_typed_id("run_legacy123abc45", "run") == "run_legacy123abc45"
