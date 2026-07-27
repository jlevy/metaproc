"""Tests for metaproc.dispatch.preflight (P2c.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from metaproc.adapters.base import AuthCapableCliAdapter, QuotaUsage
from metaproc.dispatch.credential_pool import EntryState, PoolBackend, PoolEntry, fingerprint_blob
from metaproc.dispatch.preflight import (
    CLAUDE_CODE_CLI_DEFAULT_TOKENS_PER_ITEM,
    check_step_preflight,
    summarize_headroom,
)


@dataclass
class _Pool:
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)

    def get_entry(self, a, lbl):
        return self.entries[(a, lbl)]

    def list_entries(self, adapter=None):
        return [e for (a, _l), e in self.entries.items() if adapter is None or a == adapter]

    def upsert_entry(self, *a, **k):
        return "etag"

    def delete_entry(self, *a, **k):
        return

    def seed(self, adapter, label, *, state, blob="b"):
        self.entries[(adapter, label)] = PoolEntry(
            adapter=adapter, label=label, blob=blob, state=state, etag="et"
        )


class _AdapterWithQuota:
    """Stub adapter whose query_quota_usage returns a fixed value per label.

    The preflight summarizer calls query_quota_usage(label), which
    technically takes a Path in the protocol but the summarizer
    passes label through for fake-friendly testing. Tests use this
    fake directly, bypassing the real adapter layer.
    """

    def __init__(self, remaining_by_label: dict[str, int], total: int = 1_000_000):
        self._r = remaining_by_label
        self._total = total

    def query_quota_usage(self, slot_dir: Path) -> QuotaUsage | None:
        # preflight.summarize_headroom passes a sentinel path of the
        # form /nonexistent/<label>; extract label from the last
        # path segment so the fake can key on it.
        label = slot_dir.name
        r = self._r.get(label)
        if r is None:
            return None
        return QuotaUsage(
            remaining_ratio=r / self._total,
            remaining_units=r,
            total_units=self._total,
            resets_at=None,
            unit_kind="tokens",
        )

    def query_live_quota(self, _slot_dir: Path) -> QuotaUsage | None:
        return None


class TestSummarizeHeadroom:
    def test_sums_remaining_across_labels(self):
        pool = _Pool()
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="active", fp=fingerprint_blob("a")),
        )
        pool.seed(
            "claude-code-cli",
            "home",
            state=EntryState(status="active", fp=fingerprint_blob("b")),
        )
        adapter = _AdapterWithQuota({"laptop": 500_000, "home": 300_000})
        h = summarize_headroom(
            cast(PoolBackend, cast(object, pool)),
            "claude-code-cli",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        assert h.pool_remaining_units == 800_000
        assert h.pool_total_units == 2_000_000
        assert h.eligible_label_count == 2

    def test_returns_none_remaining_when_any_label_unknown(self):
        pool = _Pool()
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="active", fp="fp"),
        )
        pool.seed(
            "claude-code-cli",
            "home",
            state=EntryState(status="active", fp="fp"),
        )
        adapter = _AdapterWithQuota({"laptop": 500_000})  # home unknown
        h = summarize_headroom(
            cast(PoolBackend, cast(object, pool)),
            "claude-code-cli",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        # Any unknown → don't fabricate a partial aggregate.
        assert h.pool_remaining_units is None

    def test_records_earliest_cooling_reset(self):
        pool = _Pool()
        pool.seed(
            "claude-code-cli",
            "cooling-a",
            state=EntryState(status="cooling", fp="fp", cooling_until_ts=100),
        )
        pool.seed(
            "claude-code-cli",
            "cooling-b",
            state=EntryState(status="cooling", fp="fp", cooling_until_ts=50),
        )
        h = summarize_headroom(cast(PoolBackend, cast(object, pool)), "claude-code-cli")
        assert h.earliest_reset_ts == 50

    def test_unregistered_adapter_returns_unknowns(self):
        pool = _Pool()
        # No adapter_impl → adapter not auth-capable → unknown.
        h = summarize_headroom(cast(PoolBackend, cast(object, pool)), "ghost-cli")
        assert h.pool_remaining_units is None


class TestCheckStepPreflight:
    def test_posture_off_short_circuits(self):
        pool = _Pool()
        verdict = check_step_preflight(
            cast(PoolBackend, cast(object, pool)),
            adapter="claude-code-cli",
            step_id="predict",
            fan_out_size=1000,
            posture="off",
        )
        assert verdict.status == "go"

    def test_missing_data_defaults_to_warn_under_warn_posture(self):
        pool = _Pool()
        verdict = check_step_preflight(
            cast(PoolBackend, cast(object, pool)),
            adapter="claude-code-cli",
            step_id="predict",
            fan_out_size=10,
            posture="warn",
        )
        # No eligible labels + no quota data → warn, but still go.
        assert verdict.status == "warn"

    def test_refuses_when_projected_exceeds_headroom(self):
        pool = _Pool()
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="active", fp="fp"),
        )
        # Tiny remaining: 100_000 tokens. Default cost 220k/item × 2
        # items × 1.2 = 528_000 projected. 528k > 0.8 × 100k = 80k.
        adapter = _AdapterWithQuota({"laptop": 100_000}, total=1_000_000)
        verdict = check_step_preflight(
            cast(PoolBackend, cast(object, pool)),
            adapter="claude-code-cli",
            step_id="predict",
            fan_out_size=2,
            posture="refuse",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        assert verdict.status == "refuse"
        assert "remediation" in verdict.message

    def test_goes_when_projected_fits(self):
        pool = _Pool()
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="active", fp="fp"),
        )
        # Huge remaining: 50M tokens. 10 items × 220k × 1.2 = 2.64M
        # projected. 2.64M <= 0.8 × 50M → go.
        adapter = _AdapterWithQuota({"laptop": 50_000_000}, total=100_000_000)
        verdict = check_step_preflight(
            cast(PoolBackend, cast(object, pool)),
            adapter="claude-code-cli",
            step_id="predict",
            fan_out_size=10,
            posture="refuse",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        assert verdict.status == "go"

    def test_default_cost_per_item_is_used_when_unset(self):
        pool = _Pool()
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="active", fp="fp"),
        )
        adapter = _AdapterWithQuota({"laptop": 1_000_000_000})  # loads of room
        verdict = check_step_preflight(
            cast(PoolBackend, cast(object, pool)),
            adapter="claude-code-cli",
            step_id="predict",
            fan_out_size=5,
            posture="refuse",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        # projected = 5 × CLAUDE_CODE_CLI_DEFAULT_TOKENS_PER_ITEM × 1.2
        expected = int(5 * CLAUDE_CODE_CLI_DEFAULT_TOKENS_PER_ITEM * 1.2)
        assert verdict.projected_units == expected

    def test_operator_cost_override_used(self):
        pool = _Pool()
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="active", fp="fp"),
        )
        adapter = _AdapterWithQuota({"laptop": 1_000_000_000})
        verdict = check_step_preflight(
            cast(PoolBackend, cast(object, pool)),
            adapter="claude-code-cli",
            step_id="predict",
            fan_out_size=5,
            mean_cost_per_item=1_000,
            posture="refuse",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
        )
        # 5 * 1000 * 1.2 = 6000
        assert verdict.projected_units == 6000

    def test_message_names_shortfall_and_remediation_under_refuse(self):
        pool = _Pool()
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(
                status="cooling",
                fp="fp",
                cooling_until_ts=1_800_000_000,
            ),
        )
        adapter = _AdapterWithQuota({})  # no usage data for cooling labels
        # With only cooling labels (past cooling_until? no, far future)
        # there are no eligible labels; missing-data → warn-or-go path.
        verdict = check_step_preflight(
            cast(PoolBackend, cast(object, pool)),
            adapter="claude-code-cli",
            step_id="predict",
            fan_out_size=1000,
            posture="refuse",
            adapter_impl=cast(AuthCapableCliAdapter, cast(object, adapter)),
            now=1_000_000_000,
        )
        # Earliest reset surfaced in the message.
        assert "2027" in verdict.message or "reset" in verdict.message.lower()
