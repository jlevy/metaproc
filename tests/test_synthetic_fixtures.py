"""Contracts for deterministic public fixture regeneration."""

from __future__ import annotations

import json
from pathlib import Path

from devtools.synthesize_fixtures import (
    PRIVATE_IPV4_PATTERN,
    TRACE_ENV_KEYS,
    fixture_paths,
    run,
    synthetic_bytes,
)

TRACE_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trace_agents"


def test_synthetic_fixture_generation_is_idempotent() -> None:
    assert run(check=True) == 0


def test_all_generated_fixtures_are_nonempty_and_canonical() -> None:
    paths = fixture_paths()
    assert paths
    for path in paths:
        assert synthetic_bytes(path)
        assert synthetic_bytes(path) == path.read_bytes()


def test_invocation_fixtures_keep_only_parser_relevant_environment() -> None:
    for path in TRACE_FIXTURE_DIR.glob("*.invocation.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert set(payload["env_redacted"]) <= TRACE_ENV_KEYS


def test_generated_fixtures_do_not_contain_private_network_addresses() -> None:
    for path in fixture_paths():
        text = path.read_text(encoding="utf-8")
        assert PRIVATE_IPV4_PATTERN.search(text) is None
