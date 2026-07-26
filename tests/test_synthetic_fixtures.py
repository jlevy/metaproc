"""Contracts for deterministic public fixture regeneration."""

from __future__ import annotations

from devtools.synthesize_fixtures import fixture_paths, run, synthetic_bytes


def test_synthetic_fixture_generation_is_idempotent() -> None:
    assert run(check=True) == 0


def test_all_generated_fixtures_are_nonempty_and_canonical() -> None:
    paths = fixture_paths()
    assert paths
    for path in paths:
        assert synthetic_bytes(path)
        assert synthetic_bytes(path) == path.read_bytes()
