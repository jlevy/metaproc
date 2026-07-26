"""Tests for the dispatch.preflight quota gate's ratio-based path.

internal-reference P1 follow-up (internal review): the preflight gate must
refuse when the Claude utilization signal says the binding window is
exhausted, even if no token-unit data is available. Before this fix,
``query_quota_usage`` populated only ``remaining_ratio`` and the gate
fell through to ``go``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from metaproc.adapters.base import AuthCapableCliAdapter, QuotaUsage
from metaproc.dispatch.credential_pool import EntryState, PoolEntry, Vehicle
from metaproc.dispatch.preflight import (
    check_step_preflight,
    summarize_headroom,
    validate_guard_posture,
)


@dataclass
class _InMemoryBackend:
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [e for (a, _l), e in self.entries.items() if adapter is None or a == adapter]

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        return self.entries[(adapter, label)]

    def upsert_entry(self, adapter, label, *, blob, state, expected_etag=None):  # noqa: ANN001, ARG002
        self.entries[(adapter, label)] = PoolEntry(
            adapter=adapter, label=label, blob=blob or "", state=state, etag="et-1"
        )
        return "et-1"

    def delete_entry(self, adapter, label):  # noqa: ANN001
        self.entries.pop((adapter, label), None)


def _seed_active(pool: _InMemoryBackend, adapter: str, label: str) -> None:
    pool.entries[(adapter, label)] = PoolEntry(
        adapter=adapter,
        label=label,
        blob="blob",
        state=EntryState(status="active", fp=f"fp-{label}", vehicle=Vehicle.OAUTH_TOKEN),
        etag="e1",
    )


@dataclass
class _RatioOnlyAdapter:
    """Stub adapter that returns ratio-but-no-units, mimicking
    ``ClaudeCodeCliAdapter.query_quota_usage`` after internal-reference."""

    ratio_by_label: dict[str, float | None]

    def query_quota_usage(self, slot_dir: Path) -> QuotaUsage | None:
        label = slot_dir.name
        ratio = self.ratio_by_label.get(label)
        if ratio is None:
            return None
        return QuotaUsage(
            remaining_ratio=ratio,
            remaining_units=None,
            total_units=None,
            resets_at=None,
            unit_kind="window-utilization",
            detail=f"label={label}",
        )

    def query_live_quota(self, _slot_dir: Path) -> QuotaUsage | None:
        return None


# ── summarize_headroom: ratio aggregation ──────────────────────


class TestSummarizeHeadroomRatio:
    def test_min_ratio_collected_across_eligible_labels(self):
        pool = _InMemoryBackend()
        _seed_active(pool, "claude-code-cli", "alt1")
        _seed_active(pool, "claude-code-cli", "alt2")
        adapter = _RatioOnlyAdapter({"alt1": 0.05, "alt2": 0.85})
        headroom = summarize_headroom(
            pool,
            "claude-code-cli",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        # Binding label is alt1 with 5% remaining.
        assert headroom.min_remaining_ratio is not None
        assert abs(headroom.min_remaining_ratio - 0.05) < 1e-6
        # Token units stay unknown — the adapter never populated them.
        assert headroom.pool_remaining_units is None

    def test_single_label_full_drives_ratio_to_zero(self):
        pool = _InMemoryBackend()
        _seed_active(pool, "claude-code-cli", "alt1")
        _seed_active(pool, "claude-code-cli", "alt2")
        adapter = _RatioOnlyAdapter({"alt1": 0.0, "alt2": 1.0})
        headroom = summarize_headroom(
            pool,
            "claude-code-cli",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        assert headroom.min_remaining_ratio == 0.0

    def test_label_returning_none_is_ignored_for_ratio(self):
        # An adapter that returns None for one label still produces
        # a valid ratio aggregate from the other(s). The min reflects
        # only labels with concrete data.
        pool = _InMemoryBackend()
        _seed_active(pool, "claude-code-cli", "alt1")
        _seed_active(pool, "claude-code-cli", "alt2")
        adapter = _RatioOnlyAdapter({"alt1": None, "alt2": 0.3})
        headroom = summarize_headroom(
            pool,
            "claude-code-cli",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        # alt1 contributed nothing, alt2 carries the ratio.
        assert headroom.min_remaining_ratio is not None
        assert abs(headroom.min_remaining_ratio - 0.3) < 1e-6


# ── check_step_preflight: ratio-only refuse path ───────────────


class TestCheckStepPreflightRatioOnly:
    """The headline regression test the reviewer asked for: a real
    ``QuotaUsage`` shape with ``remaining_ratio < 0.2`` must drive
    ``check_step_preflight(..., posture="refuse")`` to ``refuse``,
    even when ``remaining_units`` is ``None``.
    """

    def test_refuse_when_binding_ratio_below_threshold(self):
        pool = _InMemoryBackend()
        _seed_active(pool, "claude-code-cli", "alt1")
        adapter = _RatioOnlyAdapter({"alt1": 0.0})  # 100% utilized
        verdict = check_step_preflight(
            pool,
            adapter="claude-code-cli",
            step_id="predict-ticker",
            fan_out_size=10,
            posture="refuse",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        assert verdict.status == "refuse"

    def test_warn_when_binding_ratio_below_threshold(self):
        pool = _InMemoryBackend()
        _seed_active(pool, "claude-code-cli", "alt1")
        adapter = _RatioOnlyAdapter({"alt1": 0.05})
        verdict = check_step_preflight(
            pool,
            adapter="claude-code-cli",
            step_id="predict-ticker",
            fan_out_size=10,
            posture="warn",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        assert verdict.status == "warn"

    def test_go_when_ratio_above_threshold_under_refuse(self):
        # 50% remaining → well above the 20% threshold → go.
        pool = _InMemoryBackend()
        _seed_active(pool, "claude-code-cli", "alt1")
        adapter = _RatioOnlyAdapter({"alt1": 0.5})
        verdict = check_step_preflight(
            pool,
            adapter="claude-code-cli",
            step_id="predict-ticker",
            fan_out_size=10,
            posture="refuse",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        assert verdict.status == "go"

    def test_off_posture_skips_ratio_check(self):
        pool = _InMemoryBackend()
        _seed_active(pool, "claude-code-cli", "alt1")
        adapter = _RatioOnlyAdapter({"alt1": 0.0})  # would refuse if checked
        verdict = check_step_preflight(
            pool,
            adapter="claude-code-cli",
            step_id="predict-ticker",
            fan_out_size=10,
            posture="off",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        assert verdict.status == "go"

    def test_go_when_pool_active_but_no_quota_data_at_all(self):
        # Adapter returns None for every label → both views unknown.
        # Refuse posture cannot prove trouble, so falls through to go
        # (preserved from pre-2026-05-09 behavior; the alternative
        # would block every dispatch lacking telemetry).
        pool = _InMemoryBackend()
        _seed_active(pool, "claude-code-cli", "alt1")
        adapter = _RatioOnlyAdapter({"alt1": None})
        verdict = check_step_preflight(
            pool,
            adapter="claude-code-cli",
            step_id="predict-ticker",
            fan_out_size=10,
            posture="refuse",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        assert verdict.status == "go"

    def test_message_surfaces_ratio_when_units_unavailable(self):
        pool = _InMemoryBackend()
        _seed_active(pool, "claude-code-cli", "alt1")
        adapter = _RatioOnlyAdapter({"alt1": 0.05})
        verdict = check_step_preflight(
            pool,
            adapter="claude-code-cli",
            step_id="predict-ticker",
            fan_out_size=10,
            posture="warn",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        # Operator sees the binding-window percent in the message.
        assert "5%" in verdict.message
        assert "binding window" in verdict.message


# ── Guard posture validation (internal review P2) ────────────────


class TestValidateGuardPosture:
    def test_accepts_off(self):
        assert validate_guard_posture("off") == "off"

    def test_accepts_warn(self):
        assert validate_guard_posture("warn") == "warn"

    def test_accepts_refuse(self):
        assert validate_guard_posture("refuse") == "refuse"

    def test_rejects_typo(self):
        with pytest.raises(ValueError, match="off|warn|refuse"):
            validate_guard_posture("refus")

    def test_rejects_uppercase(self):
        with pytest.raises(ValueError):
            validate_guard_posture("WARN")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_guard_posture("")
