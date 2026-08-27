"""Unit tests for the hierarchical PathTally helper.

Spec: src/metaproc/docs/metaproc-design.md
§3.4 + §4.4.3.
"""

from __future__ import annotations

import pytest

from metaproc.stats.path_tally import (
    CANONICAL_SEPARATOR,
    PathTally,
    parse_canonical,
    render_canonical,
)


def test_canonical_round_trip() -> None:
    path = ("waiting", "throttling", "rate_limits", "api")
    assert parse_canonical(render_canonical(path)) == path


def test_canonical_normalises_whitespace() -> None:
    assert parse_canonical("  waiting >throttling > rate_limits>api ") == (
        "waiting",
        "throttling",
        "rate_limits",
        "api",
    )


def test_canonical_drops_empty_segments() -> None:
    assert parse_canonical("waiting > > api") == ("waiting", "api")
    assert parse_canonical(">>>") == ()


def test_canonical_separator_is_spec_form() -> None:
    assert CANONICAL_SEPARATOR == " > "


def test_add_accrues_to_every_prefix() -> None:
    tally: PathTally[float] = PathTally()
    tally.add("time_kind_path", ("waiting", "throttling", "rate_limits", "api"), 4.5)

    assert tally.get("time_kind_path", ("waiting",)) == 4.5
    assert tally.get("time_kind_path", ("waiting", "throttling")) == 4.5
    assert tally.get("time_kind_path", ("waiting", "throttling", "rate_limits")) == 4.5
    assert tally.get("time_kind_path", ("waiting", "throttling", "rate_limits", "api")) == 4.5


def test_add_sums_across_calls() -> None:
    tally: PathTally[float] = PathTally()
    tally.add("time_kind_path", ("waiting", "throttling", "rate_limits", "api"), 4.5)
    tally.add("time_kind_path", ("waiting", "throttling", "budget"), 2.0)

    # Common prefixes accumulate.
    assert tally.get("time_kind_path", ("waiting",)) == pytest.approx(6.5)
    assert tally.get("time_kind_path", ("waiting", "throttling")) == pytest.approx(6.5)
    # Independent leaves stay independent.
    assert tally.get(
        "time_kind_path", ("waiting", "throttling", "rate_limits", "api")
    ) == pytest.approx(4.5)
    assert tally.get("time_kind_path", ("waiting", "throttling", "budget")) == pytest.approx(2.0)


def test_add_from_text_matches_add() -> None:
    a: PathTally[int] = PathTally()
    b: PathTally[int] = PathTally()
    a.add("provider_path", ("provider", "anthropic"), 7)
    b.add_from_text("provider_path", "provider > anthropic", 7)
    assert dict(a.iter_prefixes("provider_path")) == dict(b.iter_prefixes("provider_path"))


def test_families_are_independent() -> None:
    tally: PathTally[float] = PathTally()
    tally.add("time_kind_path", ("waiting", "throttling"), 3.0)
    tally.add("provider_path", ("provider", "anthropic"), 3.0)
    tally.add("model_path", ("model", "claude", "opus-4.7"), 3.0)

    # Same value contributed three times (once per family) but they don't bleed across.
    assert tally.get("time_kind_path", ("waiting",)) == 3.0
    assert tally.get("provider_path", ("waiting",)) == 0  # default
    assert tally.get("model_path", ("provider",)) == 0  # default
    assert sorted(tally.families()) == ["model_path", "provider_path", "time_kind_path"]


def test_get_missing_returns_zero() -> None:
    tally: PathTally[int] = PathTally()
    assert tally.get("nonexistent", ("anything",)) == 0


def test_iter_prefixes_yields_all_recorded_prefixes_sorted() -> None:
    tally: PathTally[int] = PathTally()
    tally.add("tool_path", ("tool", "arena", "get_bundle"), 1)
    tally.add("tool_path", ("tool", "arena", "get_financial_statements"), 2)
    prefixes = dict(tally.iter_prefixes("tool_path"))
    assert prefixes == {
        ("tool",): 3,
        ("tool", "arena"): 3,
        ("tool", "arena", "get_bundle"): 1,
        ("tool", "arena", "get_financial_statements"): 2,
    }


def test_works_for_int_and_float() -> None:
    int_tally: PathTally[int] = PathTally()
    int_tally.add("counts", ("a", "b"), 3)
    int_tally.add("counts", ("a", "b"), 2)
    assert int_tally.get("counts", ("a",)) == 5

    float_tally: PathTally[float] = PathTally()
    float_tally.add("seconds", ("a", "b"), 3.5)
    float_tally.add("seconds", ("a", "b"), 2.25)
    assert float_tally.get("seconds", ("a",)) == pytest.approx(5.75)


def test_empty_path_is_noop() -> None:
    tally: PathTally[int] = PathTally()
    tally.add("counts", (), 5)
    assert tally.get("counts", ()) == 0
    assert list(tally.iter_prefixes("counts")) == []
