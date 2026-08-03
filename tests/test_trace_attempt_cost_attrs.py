"""P1.6 — attempt_cost_attrs helper + per-extractor cost stamping.

Tests that `attempt_cost_attrs` in common.py builds the canonical
`attempt.cost_usd`, `attempt.cost_provenance`, `attempt.cost_is_estimated`, and
`attempt.tokens_*` attributes, and that each adapter extractor stamps them on
attempt spans.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import metaproc.trace.extractors.common as _common_mod
from metaproc.trace.extractors.claude_agent import ClaudeAgentExtractor
from metaproc.trace.extractors.codex_agent import CodexAgentExtractor
from metaproc.trace.extractors.common import attempt_cost_attrs
from metaproc.trace.extractors.gemini_agent import GeminiAgentExtractor
from metaproc.trace.extractors.pi_agent import PiAgentExtractor


def _patch_pricing(mock_pricing: dict[str, dict[str, Any]]):
    """Patch load_pricing and clear the lazy cache so the mock is used."""
    _common_mod._pricing_cache = None
    return patch.object(_common_mod, "load_pricing", return_value=mock_pricing)


@pytest.fixture(autouse=True)
def _reset_pricing_cache():
    """Reset the pricing cache before and after each test."""
    _common_mod._pricing_cache = None
    yield
    _common_mod._pricing_cache = None


# ── attempt_cost_attrs helper ──


def test_self_reported_cost_preferred_over_pricing_table() -> None:
    """A self-reported figure wins over the pricing table, as a client estimate."""
    result = attempt_cost_attrs(
        model="claude-opus-4-7",
        provider="anthropic",
        input_tokens=1000,
        output_tokens=200,
        cached_tokens=50,
        self_reported_cost=0.42,
    )
    assert result["attempt.cost_usd"] == 0.42
    assert result["attempt.cost_is_estimated"] is True
    assert result["attempt.cost_provenance"] == "client_list_estimate"
    assert result["attempt.tokens_input"] == 1000
    assert result["attempt.tokens_output"] == 200
    assert result["attempt.tokens_cached"] == 50


def test_zero_self_reported_cost_is_still_self_reported() -> None:
    """A cost of 0.0 from the adapter is a real self-report, not a missing value."""
    result = attempt_cost_attrs(
        model="glm-5-maas",
        provider="vertex-maas",
        input_tokens=500,
        output_tokens=100,
        cached_tokens=0,
        self_reported_cost=0.0,
    )
    assert result["attempt.cost_usd"] == 0.0
    assert result["attempt.cost_is_estimated"] is True
    assert result["attempt.cost_provenance"] == "client_list_estimate"


def test_pricing_table_fallback_when_no_self_reported_cost() -> None:
    """When self_reported_cost is None, compute from pricing table."""
    # Mock load_pricing to return a known table
    mock_pricing = {
        "test-model": {
            "actual_price": {"input_per_1m": 10.0, "output_per_1m": 30.0, "cache_read_per_1m": 1.0},
            "provider": "test",
        }
    }
    with _patch_pricing(mock_pricing):
        result = attempt_cost_attrs(
            model="test-model",
            provider="test",
            input_tokens=1_000_000,
            output_tokens=100_000,
            cached_tokens=500_000,
            self_reported_cost=None,
        )
    # Expected: 10.0 + 3.0 + 0.5 = 13.5
    assert result["attempt.cost_usd"] == pytest.approx(13.5)
    assert result["attempt.cost_is_estimated"] is True


def test_unknown_model_no_cost_attrs() -> None:
    """When model is unknown and no self-reported cost, cost attrs are absent."""
    mock_pricing: dict[str, dict[str, Any]] = {}
    with _patch_pricing(mock_pricing):
        result = attempt_cost_attrs(
            model="mystery-model-9000",
            provider=None,
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            self_reported_cost=None,
        )
    assert "attempt.cost_usd" not in result
    assert "attempt.cost_is_estimated" not in result
    # Token attrs should still be present
    assert result["attempt.tokens_input"] == 100
    assert result["attempt.tokens_output"] == 50
    assert result["attempt.tokens_cached"] == 0


def test_none_tokens_omitted() -> None:
    """When token counts are None, they are omitted from result."""
    result = attempt_cost_attrs(
        model=None,
        provider=None,
        input_tokens=None,
        output_tokens=None,
        cached_tokens=None,
        self_reported_cost=0.10,
    )
    assert result["attempt.cost_usd"] == 0.10
    assert result["attempt.cost_is_estimated"] is True
    assert result["attempt.cost_provenance"] == "client_list_estimate"
    assert "attempt.tokens_input" not in result
    assert "attempt.tokens_output" not in result
    assert "attempt.tokens_cached" not in result


def test_no_model_no_self_reported_no_cost() -> None:
    """No model + no self-reported cost = no cost attrs."""
    mock_pricing: dict[str, dict[str, Any]] = {}
    with _patch_pricing(mock_pricing):
        result = attempt_cost_attrs(
            model=None,
            provider=None,
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            self_reported_cost=None,
        )
    assert "attempt.cost_usd" not in result
    assert "attempt.cost_is_estimated" not in result


# ── Claude extractor: cost attrs on attempt span ──


def _write_claude_log(
    run_dir: Path,
    *,
    total_cost_usd: float = 0.42,
) -> Path:
    """Write a minimal claude JSONL with a result event carrying total_cost_usd."""
    path = run_dir / ".logs" / "tasks" / "step" / "item" / "log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x"}}
                ]
            },
            "session_id": "s1",
            "timestamp": "2026-05-12T00:00:01Z",
        },
        {
            "type": "user",
            "message": {"content": [{"tool_use_id": "t1", "type": "tool_result", "content": "ok"}]},
            "session_id": "s1",
            "timestamp": "2026-05-12T00:00:02Z",
        },
        {
            "type": "result",
            "total_cost_usd": total_cost_usd,
            "num_turns": 1,
            "duration_ms": 1000,
            "session_id": "s1",
            "is_error": False,
        },
    ]
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


def test_claude_attempt_carries_canonical_cost_attrs(tmp_path: Path) -> None:
    """Claude's result.total_cost_usd maps to attempt.cost_usd as a client estimate.

    Claude Code computes it locally from a bundled price table, so it is not a
    provider-authoritative amount.
    """

    run_dir = tmp_path / "run"
    _write_claude_log(run_dir, total_cost_usd=1.23)
    extractor = ClaudeAgentExtractor()
    spans = list(extractor.extract(run_dir, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    # Legacy key preserved
    assert attempt.attributes["attempt.total_cost_usd"] == 1.23
    # Canonical keys
    assert attempt.attributes["attempt.cost_usd"] == 1.23
    assert attempt.attributes["attempt.cost_is_estimated"] is True
    assert attempt.attributes["attempt.cost_provenance"] == "client_list_estimate"


# ── Codex extractor: cost attrs on attempt span ──


def _write_codex_log(
    run_dir: Path,
    *,
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cached_tokens: int = 50,
    model: str = "gpt-5.5",
    auth_route: str = "api_key",
) -> Path:
    """Write a minimal codex JSONL with turn.completed usage."""
    path = run_dir / ".logs" / "tasks" / "step" / "item" / "log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {"type": "thread.started", "thread_id": "tid"},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached_tokens,
            },
        },
    ]
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    # Write sidecar with adapter.model
    sidecar = path.with_suffix(".jsonl.invocation.json")
    sidecar.write_text(
        json.dumps(
            {
                "argv": ["codex"],
                "metadata": {
                    "adapter_model": model,
                    "adapter_provider": "openai",
                    "auth_route": auth_route,
                },
            }
        )
    )
    return path


def _codex_attempt(tmp_path: Path, *, auth_route: str):
    run_dir = tmp_path / "run"
    _write_codex_log(
        run_dir,
        input_tokens=1_000_000,
        output_tokens=100_000,
        cached_tokens=0,
        model="gpt-5.5",
        auth_route=auth_route,
    )
    mock_pricing = {
        "gpt-5.5": {
            "actual_price": {"input_per_1m": 5.0, "output_per_1m": 30.0},
            "provider": "openai",
        },
    }
    with _patch_pricing(mock_pricing):
        spans = list(CodexAgentExtractor().extract(run_dir, trace_id="t"))
    return next(s for s in spans if s.kind == "attempt")


def test_codex_api_key_attempt_carries_cost_from_pricing_table(tmp_path: Path) -> None:
    """Codex has no self-reported cost; cost comes from pricing table as estimated."""

    attempt = _codex_attempt(tmp_path, auth_route="api_key")
    assert attempt.attributes["attempt.tokens_input"] == 1_000_000
    assert attempt.attributes["attempt.tokens_output"] == 100_000
    assert attempt.attributes["attempt.tokens_cached"] == 0
    # 5.0 + 3.0 = 8.0
    assert attempt.attributes["attempt.cost_usd"] == pytest.approx(8.0)
    assert attempt.attributes["attempt.cost_is_estimated"] is True


def test_codex_chatgpt_plan_attempt_records_route_without_claiming_charge(
    tmp_path: Path,
) -> None:
    """Same tokens on a ChatGPT-plan route: identical quantity, same estimate.

    The route is recorded, but it does not move the dollars or assert anything
    about what was charged — plan users can buy credits and continue past the
    included allowance on the same authentication.
    """
    attempt = _codex_attempt(tmp_path, auth_route="chatgpt_plan")
    assert attempt.attributes["attempt.tokens_input"] == 1_000_000
    assert attempt.attributes["attempt.tokens_output"] == 100_000
    assert attempt.attributes["attempt.cost_usd"] == pytest.approx(8.0)
    assert attempt.attributes["attempt.cost_provenance"] == "pricing_table_estimate"
    assert attempt.attributes["attempt.charge_status"] == "unknown"
    assert attempt.attributes["attempt.auth_route"] == "chatgpt_plan"


# ── Gemini extractor: cost attrs on attempt span ──


def _write_gemini_log(
    run_dir: Path,
    *,
    models: dict[str, dict[str, int]] | None = None,
    model_name: str = "gemini-3.5-flash",
) -> Path:
    """Write a minimal gemini JSONL with result.stats.models."""
    path = run_dir / ".logs" / "tasks" / "step" / "item" / "log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if models is None:
        models = {model_name: {"input_tokens": 500, "output_tokens": 100, "cached": 50}}
    events = [
        {
            "type": "init",
            "model": model_name,
            "timestamp": "2026-01-01T00:00:00Z",
            "session_id": "s1",
        },
        {
            "type": "result",
            "status": "success",
            "timestamp": "2026-01-01T00:00:10Z",
            "stats": {"duration_ms": 5000, "models": models},
        },
    ]
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


def test_gemini_attempt_carries_cost_from_pricing_table(tmp_path: Path) -> None:
    """Gemini has no self-reported cost; cost computed from pricing table."""

    run_dir = tmp_path / "run"
    _write_gemini_log(
        run_dir,
        models={
            "gemini-3.5-flash": {
                "input_tokens": 1_000_000,
                "output_tokens": 100_000,
                "cached": 500_000,
            }
        },
    )
    mock_pricing = {
        "gemini-3.5-flash": {
            "actual_price": {
                "input_per_1m": 0.15,
                "output_per_1m": 0.60,
                "cache_read_per_1m": 0.0375,
            },
            "provider": "google",
        },
    }
    with _patch_pricing(mock_pricing):
        extractor = GeminiAgentExtractor()
        spans = list(extractor.extract(run_dir, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes["attempt.tokens_input"] == 1_000_000
    assert attempt.attributes["attempt.tokens_output"] == 100_000
    assert attempt.attributes["attempt.tokens_cached"] == 500_000
    # 0.15 + 0.06 + 0.01875 = 0.22875
    assert attempt.attributes["attempt.cost_usd"] == pytest.approx(0.22875)
    assert attempt.attributes["attempt.cost_is_estimated"] is True


# ── Pi extractor: cost attrs already wired, confirm canonical keys ──


def _write_pi_log_with_cost(
    run_dir: Path,
    *,
    cost_total: float | None = 0.05,
    model: str = "claude-opus-4-6",
) -> Path:
    """Write a minimal pi JSONL with agent_end carrying usage + optional cost."""
    path = run_dir / ".logs" / "tasks" / "step" / "item" / "log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    usage: dict[str, Any] = {"input": 1000, "output": 200, "cacheRead": 50, "cacheWrite": 10}
    if cost_total is not None:
        usage["cost"] = {"total": cost_total}
    events = [
        {"type": "session", "version": 3, "id": "sess1"},
        {"type": "agent_start"},
        {
            "type": "agent_end",
            "messages": [{"role": "assistant", "model": model, "usage": usage}],
        },
    ]
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


def test_pi_self_reported_cost_uses_canonical_keys(tmp_path: Path) -> None:
    """Pi with cost.total present uses attempt.cost_usd with is_estimated=False."""

    run_dir = tmp_path / "run"
    _write_pi_log_with_cost(run_dir, cost_total=0.05)
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(run_dir, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes["attempt.cost_usd"] == 0.05
    assert attempt.attributes["attempt.cost_is_estimated"] is True
    assert attempt.attributes["attempt.cost_provenance"] == "client_list_estimate"


def test_pi_no_cost_falls_back_to_pricing_table(tmp_path: Path) -> None:
    """Pi without cost.total computes from pricing table."""

    run_dir = tmp_path / "run"
    _write_pi_log_with_cost(run_dir, cost_total=None, model="test-model")
    mock_pricing = {
        "test-model": {
            "actual_price": {"input_per_1m": 10.0, "output_per_1m": 30.0, "cache_read_per_1m": 1.0},
            "provider": "test",
        }
    }
    with _patch_pricing(mock_pricing):
        extractor = PiAgentExtractor()
        spans = list(extractor.extract(run_dir, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    # 1000 * 10/1M + 200 * 30/1M + 50 * 1/1M = 0.01 + 0.006 + 0.00005 = 0.01605
    assert attempt.attributes["attempt.cost_usd"] == pytest.approx(0.01605)
    assert attempt.attributes["attempt.cost_is_estimated"] is True
