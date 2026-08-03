"""Auth-route resolution and cost-provenance separation in ``--cost``.

The invariants under test:

- A credential route is only reported when it is provable; ``unknown`` is a
  valid and expected answer.
- Charge status is never asserted from a run artifact.
- Dollar figures obtained in different ways never sum into one total.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import metaproc.trace.extractors.common as common_mod
from metaproc.adapters.billing import (
    UNKNOWN,
    default_cost_provenance,
    resolve_auth_route,
    resolve_charge_status,
)
from metaproc.trace.extractors.claude_agent import ClaudeAgentExtractor
from metaproc.trace.extractors.common import attempt_cost_attrs, auth_route_from_invocation
from metaproc.trace.extractors.pi_agent import PiAgentExtractor
from metaproc.trace.named_views import cost_rows, format_cost
from metaproc.trace.schema import SpanKind, TraceEvent

FIXTURES = Path(__file__).parent / "fixtures" / "trace_agents"


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


class TestClaudeAuthRoute:
    def test_cloud_routing_wins_over_keys(self) -> None:
        env = {"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_API_KEY": "sk-x"}
        assert resolve_auth_route("claude-code-cli", env) == "bedrock"
        assert resolve_auth_route("claude-code-cli", {"CLAUDE_CODE_USE_VERTEX": "true"}) == "vertex"

    def test_auth_token_is_a_gateway_not_an_api_key(self) -> None:
        assert resolve_auth_route("claude-code-cli", {"ANTHROPIC_AUTH_TOKEN": "t"}) == "gateway"

    def test_api_key_then_plan_oauth(self) -> None:
        assert resolve_auth_route("claude-code-cli", {"ANTHROPIC_API_KEY": "sk-x"}) == "api_key"
        assert (
            resolve_auth_route("claude-code-cli", {"CLAUDE_CODE_OAUTH_TOKEN": "t"}) == "claude_plan"
        )

    def test_stored_login_and_api_key_helper_are_unknown(self) -> None:
        # Vehicle B leaves no env trace and apiKeyHelper is a settings hook;
        # neither is guessable, so neither may be asserted.
        assert resolve_auth_route("claude-code-cli", {}) == UNKNOWN


class TestCodexAuthRoute:
    def test_codex_api_key_is_the_per_invocation_override(self) -> None:
        assert resolve_auth_route("codex-cli", {"CODEX_API_KEY": "sk-x"}) == "api_key"

    def test_ambient_openai_api_key_alone_proves_nothing(self) -> None:
        # OPENAI_API_KEY feeds `codex login --with-api-key`; it is not what
        # `codex exec` reads, so it cannot settle the effective route.
        assert resolve_auth_route("codex-cli", {"OPENAI_API_KEY": "sk-x"}) == UNKNOWN

    def test_persisted_auth_json_settles_the_route(self, tmp_path: Path) -> None:
        home = tmp_path / ".codex"
        home.mkdir()
        for mode, expected in (("apikey", "api_key"), ("chatgpt", "chatgpt_plan")):
            (home / "auth.json").write_text(json.dumps({"tokens": {"auth_mode": mode}}))
            assert resolve_auth_route("codex-cli", {"CODEX_HOME": str(home)}) == expected

    def test_unreadable_auth_json_is_unknown(self, tmp_path: Path) -> None:
        assert resolve_auth_route("codex-cli", {"CODEX_HOME": str(tmp_path / "nope")}) == UNKNOWN


class TestOtherAdapterRoutes:
    def test_gemini_vertex_adc_is_not_a_plan_route(self) -> None:
        # Vertex + ADC is the recommended mode and carries no API key; absence
        # of a key must not read as a subscription.
        assert resolve_auth_route("gemini-cli", {"GOOGLE_GENAI_USE_VERTEXAI": "true"}) == "vertex"
        assert resolve_auth_route("gemini-cli", {}) == UNKNOWN

    def test_gemini_api_key(self) -> None:
        assert resolve_auth_route("gemini-cli", {"GEMINI_API_KEY": "x"}) == "api_key"
        assert resolve_auth_route("gemini-cli", {"GOOGLE_API_KEY": "x"}) == "api_key"

    def test_pi_delegates_to_its_provider(self) -> None:
        assert resolve_auth_route("pi-cli", {"GOOGLE_GENAI_USE_VERTEXAI": "1"}) == "vertex"
        assert resolve_auth_route("pi-cli", {"ANTHROPIC_API_KEY": "sk-x"}) == "api_key"
        # No key and no vertex flag: the provider is chosen in pi's own config.
        assert resolve_auth_route("pi-cli", {}) == UNKNOWN

    def test_unregistered_adapter_is_unknown(self) -> None:
        assert resolve_auth_route("mystery-cli", {"ANTHROPIC_API_KEY": "x"}) == UNKNOWN


class TestChargeStatusIsNeverAsserted:
    def test_every_route_yields_unknown(self) -> None:
        for route in ("api_key", "chatgpt_plan", "claude_plan", "vertex", None):
            assert resolve_charge_status(route) == UNKNOWN

    def test_self_reported_agent_cost_is_a_client_estimate(self) -> None:
        assert default_cost_provenance("api_key", self_reported=True) == "client_list_estimate"
        assert default_cost_provenance("claude_plan", self_reported=True) == "client_list_estimate"


class TestAuthRouteFromInvocation:
    def test_prefers_stamped_metadata(self) -> None:
        invocation = {
            "metadata": {"adapter_type": "claude-code-cli", "auth_route": "bedrock"},
            "env_redacted": {},
        }
        assert auth_route_from_invocation(invocation) == "bedrock"

    def test_recorded_adapter_type_wins_over_the_reading_extractor(self) -> None:
        # Legacy Claude-shaped codex logs are read by ClaudeAgentExtractor;
        # resolving them against Claude's credential chain would be wrong.
        invocation = {
            "metadata": {"adapter_type": "codex-cli"},
            "env_redacted": {"ANTHROPIC_API_KEY": "<redacted len=51>"},
        }
        assert auth_route_from_invocation(invocation, adapter_type="claude-code-cli") == UNKNOWN

    def test_redacted_empty_value_reads_as_absent(self) -> None:
        # Sidecars redact an empty var to the truthy string "<redacted len=0>";
        # an empty key activates no route in any CLI.
        invocation = {
            "metadata": {"adapter_type": "claude-code-cli"},
            "env_redacted": {"ANTHROPIC_API_KEY": "<redacted len=0>"},
        }
        assert auth_route_from_invocation(invocation) == UNKNOWN

    def test_redacted_nonempty_value_reads_as_present(self) -> None:
        invocation = {
            "metadata": {"adapter_type": "claude-code-cli"},
            "env_redacted": {"ANTHROPIC_API_KEY": "<redacted len=51>"},
        }
        assert auth_route_from_invocation(invocation) == "api_key"

    def test_missing_invocation_yields_none(self) -> None:
        assert auth_route_from_invocation(None) is None


class TestClaudeTokenExtraction:
    """The token story is only true if the tokens are actually extracted."""

    def _attempt(self, tmp_path: Path) -> TraceEvent:
        tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
        tasks.mkdir(parents=True)
        (tasks / "log.jsonl").write_text((FIXTURES / "claude-sample.jsonl").read_text())
        spans = list(ClaudeAgentExtractor().extract(tmp_path, trace_id="t"))
        return next(s for s in spans if s.kind == "attempt")

    def _result_event(self) -> dict[str, Any]:
        for line in (FIXTURES / "claude-sample.jsonl").read_text().splitlines():
            event = json.loads(line)
            if event.get("type") == "result":
                return event
        raise AssertionError("fixture has no result event")

    def test_real_fixture_yields_nonzero_tokens(self, tmp_path: Path) -> None:
        attrs = self._attempt(tmp_path).attributes
        assert attrs["attempt.tokens_input"] > 0
        assert attrs["attempt.tokens_output"] > 0
        assert attrs["attempt.tokens_cached"] > 0
        assert attrs["attempt.tokens_cache_write"] > 0

    def test_totals_come_from_modelusage_so_subagents_are_included(self, tmp_path: Path) -> None:
        result = self._result_event()
        expected_out = sum(u["outputTokens"] for u in result["modelUsage"].values())
        attrs = self._attempt(tmp_path).attributes
        assert attrs["attempt.tokens_output"] == expected_out
        # result.usage excludes subagent activity, so it must not be the source.
        assert expected_out != result["usage"]["output_tokens"]
        assert attrs["attempt.models"] == ",".join(sorted(result["modelUsage"]))

    def test_client_reported_cost_is_marked_estimated(self, tmp_path: Path) -> None:
        attrs = self._attempt(tmp_path).attributes
        assert attrs["attempt.cost_provenance"] == "client_list_estimate"
        assert attrs["attempt.cost_is_estimated"] is True
        assert attrs["attempt.charge_status"] == UNKNOWN

    def test_rendered_claude_totals_are_nonzero(self, tmp_path: Path) -> None:
        rows = cost_rows([self._attempt(tmp_path)])
        claude = next(r for r in rows if r["source"] == "claude-agent")
        assert claude["tokens_in"] > 0
        assert claude["tokens_out"] > 0


class TestAttemptCostAttrs:
    def test_dollars_always_land_in_cost_usd_qualified_by_provenance(self) -> None:
        for route in ("claude_plan", "api_key", UNKNOWN):
            attrs = attempt_cost_attrs(
                model="claude-opus-4-8",
                provider="anthropic",
                input_tokens=100,
                output_tokens=200,
                cached_tokens=0,
                self_reported_cost=1.25,
                auth_route=route,
            )
            assert attrs["attempt.cost_usd"] == 1.25
            assert attrs["attempt.cost_provenance"] == "client_list_estimate"
            assert attrs["attempt.charge_status"] == UNKNOWN

    def test_provider_authoritative_is_not_estimated(self) -> None:
        attrs = attempt_cost_attrs(
            model="m",
            provider="exa",
            input_tokens=None,
            output_tokens=None,
            cached_tokens=None,
            self_reported_cost=0.42,
            auth_route="api_key",
            cost_provenance="provider_authoritative",
        )
        assert attrs["attempt.cost_is_estimated"] is False

    def test_tokens_recorded_on_every_route(self) -> None:
        for route in ("claude_plan", "api_key", UNKNOWN):
            attrs = attempt_cost_attrs(
                model="m",
                provider="p",
                input_tokens=7,
                output_tokens=11,
                cached_tokens=3,
                self_reported_cost=None,
                auth_route=route,
            )
            assert attrs["attempt.tokens_input"] == 7
            assert attrs["attempt.tokens_output"] == 11
            assert attrs["attempt.tokens_cached"] == 3


class TestCostView:
    def _mixed_spans(self) -> list[TraceEvent]:
        return [
            _span(
                "fintool",
                {"cost.usd": 2.50, "attempt.cost_provenance": "provider_authoritative"},
                kind="provider_call",
            ),
            _span(
                "claude-agent",
                {
                    "attempt.cost_usd": 100.0,
                    "attempt.cost_provenance": "client_list_estimate",
                    "attempt.tokens_input": 1000,
                    "attempt.tokens_output": 2000,
                },
            ),
            _span(
                "codex-agent",
                {
                    "attempt.cost_usd": 0.50,
                    "attempt.cost_provenance": "pricing_table_estimate",
                },
            ),
        ]

    def test_json_shape_stays_a_top_level_array(self) -> None:
        rows = cost_rows(self._mixed_spans())
        assert isinstance(rows, list)
        # Historical keys survive for existing consumers.
        assert {"source", "kind", "cost", "count"} <= set(rows[0])

    def test_rows_carry_provenance_and_tokens(self) -> None:
        rows = cost_rows(self._mixed_spans())
        claude = next(r for r in rows if r["source"] == "claude-agent")
        assert claude["cost_provenance"] == "client_list_estimate"
        assert claude["tokens_in"] == 1000
        assert claude["tokens_out"] == 2000

    def test_render_subtotals_per_provenance_with_no_grand_total(self) -> None:
        out = format_cost(cost_rows(self._mixed_spans()))
        assert out.count("subtotal:") == 3
        assert "$2.5000" in out
        assert "$100.0000" in out
        assert "$0.5000" in out
        # 103.00 would be the meaningless combined figure.
        assert "103.0000" not in out
        assert "total cost across trace" not in out

    def test_render_never_asserts_whether_money_is_owed(self) -> None:
        out = format_cost(cost_rows(self._mixed_spans()))
        assert "charge status is unknown" in out
        assert "money owed" not in out

    def test_empty_trace(self) -> None:
        assert format_cost(cost_rows([])) == "(no cost data in trace)\n"


class TestPluginSuppliedCostProvenance:
    """Spans carrying top-level ``cost.usd`` come from plugins, not adapters."""

    def test_cost_kind_maps_onto_provenance(self) -> None:
        rows = cost_rows(
            [
                _span("fintool", {"cost.usd": 2.0, "cost.kind": "actual"}, kind="provider_call"),
                _span("arena", {"cost.usd": 3.0, "cost.kind": "estimated"}, kind="provider_call"),
            ]
        )
        by_source = {r["source"]: r["cost_provenance"] for r in rows}
        assert by_source["fintool"] == "provider_authoritative"
        assert by_source["arena"] == "pricing_table_estimate"

    def test_explicit_cost_provenance_wins(self) -> None:
        rows = cost_rows(
            [
                _span(
                    "plugin",
                    {"cost.usd": 1.0, "cost.provenance": "provider_authoritative"},
                    kind="provider_call",
                )
            ]
        )
        assert rows[0]["cost_provenance"] == "provider_authoritative"

    def test_undeclared_provenance_is_unknown_not_assumed_authoritative(self) -> None:
        # The failure mode to avoid: an unlabeled amount silently counted as
        # provider-reported spend.
        rows = cost_rows([_span("legacy", {"cost.usd": 5.0}, kind="provider_call")])
        assert rows[0]["cost_provenance"] == UNKNOWN

    def test_unknown_group_is_rendered_with_a_real_heading(self) -> None:
        out = format_cost(cost_rows([_span("legacy", {"cost.usd": 5.0}, kind="provider_call")]))
        assert "Unknown provenance" in out
        assert "(none)" not in out


class TestPiFallbackKeepsProvenance:
    def test_pricing_table_fallback_carries_every_cost_key(self, tmp_path: Path) -> None:
        """pi's fallback must not drop provenance on the way out of the helper."""
        path = tmp_path / ".logs" / "tasks" / "step" / "item" / "log.jsonl"
        path.parent.mkdir(parents=True)
        events = [
            {"type": "session", "id": "s1"},
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "test-model",
                        "usage": {"input": 1_000_000, "output": 100_000},
                    }
                ],
            },
        ]
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        pricing = {
            "test-model": {
                "actual_price": {"input_per_1m": 5.0, "output_per_1m": 30.0},
                "provider": "test",
            }
        }
        common_mod._pricing_cache = None
        with patch.object(common_mod, "load_pricing", return_value=pricing):
            spans = list(PiAgentExtractor().extract(tmp_path, trace_id="t"))
        common_mod._pricing_cache = None

        attrs = next(s for s in spans if s.kind == "attempt").attributes
        assert attrs["attempt.cost_usd"] > 0
        assert attrs["attempt.cost_provenance"] == "pricing_table_estimate"
        assert attrs["attempt.charge_status"] == UNKNOWN
