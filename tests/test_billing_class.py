"""Billing-class resolution and the metered/subscription split in ``--cost``.

The invariant under test: a dollar figure produced under a flat plan fee is
never reported as, or summed into, metered spend.
"""

from __future__ import annotations

from metaproc.adapters.billing import METERED, SUBSCRIPTION, UNKNOWN, resolve_billing_class
from metaproc.trace.extractors.common import attempt_cost_attrs, billing_class_from_invocation
from metaproc.trace.named_views import cost_rows, format_cost
from metaproc.trace.schema import SpanKind, TraceEvent


def _span(source: str, attributes: dict[str, object], *, kind: SpanKind = "attempt") -> TraceEvent:
    return TraceEvent(
        trace_id="t",
        span_id=f"s-{source}-{len(attributes)}",
        parent_span_id=None,
        name=source,
        kind=kind,
        source=source,
        ts_start="2026-08-02T00:00:00Z",
        ts_end="2026-08-02T00:00:01Z",
        duration_ms=1000.0,
        status="ok",
        attributes=attributes,
    )


class TestResolveBillingClass:
    def test_no_api_key_is_subscription(self) -> None:
        assert resolve_billing_class("claude-code-cli", {}) == SUBSCRIPTION
        assert resolve_billing_class("codex-cli", {}) == SUBSCRIPTION

    def test_api_key_present_flips_to_metered(self) -> None:
        assert resolve_billing_class("claude-code-cli", {"ANTHROPIC_API_KEY": "sk-x"}) == METERED
        assert resolve_billing_class("codex-cli", {"OPENAI_API_KEY": "sk-x"}) == METERED

    def test_empty_value_does_not_count_as_present(self) -> None:
        # The CLIs treat an empty value as unset; so must we, or a blank export
        # would silently reclassify a whole run.
        assert resolve_billing_class("claude-code-cli", {"ANTHROPIC_API_KEY": ""}) == SUBSCRIPTION

    def test_gemini_honors_either_key(self) -> None:
        assert resolve_billing_class("gemini-cli", {"GOOGLE_API_KEY": "x"}) == METERED
        assert resolve_billing_class("gemini-cli", {"GEMINI_API_KEY": "x"}) == METERED

    def test_pi_has_no_subscription_vehicle(self) -> None:
        assert resolve_billing_class("pi-cli", {}) == METERED

    def test_unregistered_adapter_is_unknown_not_guessed(self) -> None:
        assert resolve_billing_class("mystery-cli", {}) == UNKNOWN


class TestBillingClassFromInvocation:
    def test_prefers_stamped_metadata(self) -> None:
        invocation = {
            "metadata": {"adapter_type": "claude-code-cli", "billing_class": "metered"},
            "env_redacted": {},
        }
        assert billing_class_from_invocation(invocation) == METERED

    def test_falls_back_to_recorded_env_key_set(self) -> None:
        # Sidecars written before the launcher stamped billing_class still
        # record which env vars were present (values redacted, names kept).
        invocation = {
            "metadata": {"adapter_type": "codex-cli"},
            "env_redacted": {"OPENAI_API_KEY": "<redacted len=51>"},
        }
        assert billing_class_from_invocation(invocation) == METERED

    def test_missing_invocation_yields_none(self) -> None:
        assert billing_class_from_invocation(None) is None


class TestAttemptCostAttrs:
    def test_subscription_cost_never_lands_in_cost_usd(self) -> None:
        attrs = attempt_cost_attrs(
            model="claude-opus-4-8",
            provider="anthropic",
            input_tokens=100,
            output_tokens=200,
            cached_tokens=0,
            self_reported_cost=1.25,
            billing_class=SUBSCRIPTION,
        )
        assert attrs["attempt.cost_list_equiv_usd"] == 1.25
        assert "attempt.cost_usd" not in attrs
        assert attrs["attempt.billing_class"] == SUBSCRIPTION

    def test_metered_cost_keeps_cost_usd(self) -> None:
        attrs = attempt_cost_attrs(
            model="claude-opus-4-8",
            provider="anthropic",
            input_tokens=100,
            output_tokens=200,
            cached_tokens=0,
            self_reported_cost=1.25,
            billing_class=METERED,
        )
        assert attrs["attempt.cost_usd"] == 1.25
        assert "attempt.cost_list_equiv_usd" not in attrs

    def test_tokens_recorded_under_both_classes(self) -> None:
        for billing_class in (SUBSCRIPTION, METERED):
            attrs = attempt_cost_attrs(
                model="m",
                provider="p",
                input_tokens=7,
                output_tokens=11,
                cached_tokens=3,
                self_reported_cost=None,
                billing_class=billing_class,
            )
            assert attrs["attempt.tokens_input"] == 7
            assert attrs["attempt.tokens_output"] == 11
            assert attrs["attempt.tokens_cached"] == 3


class TestCostView:
    def _mixed_spans(self) -> list[TraceEvent]:
        return [
            # Real metered provider spend.
            _span("fintool", {"cost.usd": 2.50}, kind="provider_call"),
            # Subscription agent: dollars are a list-price equivalent.
            _span(
                "claude-agent",
                {
                    "attempt.billing_class": SUBSCRIPTION,
                    "attempt.cost_list_equiv_usd": 100.0,
                    "attempt.tokens_input": 1000,
                    "attempt.tokens_output": 2000,
                    "adapter.model": "claude-opus-4-8",
                },
            ),
            # Metered agent: an API key was in scope, so this is money.
            _span(
                "codex-agent",
                {
                    "attempt.billing_class": METERED,
                    "attempt.cost_usd": 0.50,
                    "adapter.model": "gpt-5.6-sol",
                },
            ),
        ]

    def test_classes_are_separated(self) -> None:
        rows = cost_rows(self._mixed_spans())
        assert set(rows) == {"metered", "subscription"}
        metered_sources = {r["source"] for r in rows["metered"]}
        assert metered_sources == {"fintool", "codex-agent"}
        assert {r["source"] for r in rows["subscription"]} == {"claude-agent"}

    def test_subscription_equivalent_excluded_from_metered_total(self) -> None:
        rows = cost_rows(self._mixed_spans())
        metered_total = sum(float(r["cost"]) for r in rows["metered"])
        # 2.50 provider + 0.50 metered agent. The $100 list-equivalent must not
        # appear here — that is the whole point of the split.
        assert metered_total == 3.00

    def test_subscription_tokens_are_reported(self) -> None:
        rows = cost_rows(self._mixed_spans())
        row = rows["subscription"][0]
        assert row["tokens_in"] == 1000
        assert row["tokens_out"] == 2000
        # Never headed "cost" — that name is reserved for money owed.
        assert row["list_equiv_usd"] == 100.0
        assert "cost" not in row

    def test_rendered_output_never_shows_a_combined_total(self) -> None:
        out = format_cost(cost_rows(self._mixed_spans()))
        assert "total metered spend: $3.0000" in out
        assert "NOT money owed" in out
        assert "total cost across trace" not in out

    def test_empty_trace(self) -> None:
        assert format_cost(cost_rows([])) == "(no cost data in trace)\n"
