"""Whole-attempt Claude usage: one normalizer, two planes, same totals.

Guards the defects from the PR #6 review: the trace plane and the resource
plane read different fields off the same `result` event and disagreed, and a
malformed `modelUsage` could shadow a perfectly good `usage` block.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.logutil.claude_usage import claude_attempt_usage
from metaproc.logutil.parsing import LogFile
from metaproc.logutil.usage import compute_cost, load_pricing, sum_codex_usage
from metaproc.trace.extractors.claude_agent import ClaudeAgentExtractor

FIXTURE = Path(__file__).parent / "fixtures" / "trace_agents" / "claude-sample.jsonl"


def _result_event() -> dict[str, Any]:
    for line in FIXTURE.read_text().splitlines():
        event = json.loads(line)
        if event.get("type") == "result":
            return event
    raise AssertionError("fixture has no result event")


class TestModelUsagePreferredWithFallback:
    def test_modelusage_wins_and_covers_the_whole_tree(self) -> None:
        result = _result_event()
        usage = claude_attempt_usage(result)
        expected_in = sum(u["inputTokens"] for u in result["modelUsage"].values())
        expected_out = sum(u["outputTokens"] for u in result["modelUsage"].values())

        assert usage.source == "modelUsage"
        assert (usage.input_tokens, usage.output_tokens) == (expected_in, expected_out)
        # Top-level usage excludes subagents, so the two must differ here or the
        # fixture no longer exercises the distinction.
        assert result["usage"]["output_tokens"] != expected_out

    def test_malformed_nonempty_modelusage_falls_back_to_usage(self) -> None:
        usage = claude_attempt_usage(
            {
                "modelUsage": {"claude-opus-4-7": "not-a-dict", "other": 17},
                "usage": {"input_tokens": 53, "output_tokens": 26134},
            }
        )
        assert usage.source == "usage"
        assert (usage.input_tokens, usage.output_tokens) == (53, 26134)

    def test_partially_valid_modelusage_still_aggregates(self) -> None:
        usage = claude_attempt_usage(
            {
                "modelUsage": {
                    "good": {"inputTokens": 10, "outputTokens": 20},
                    "bad": None,
                },
                "usage": {"input_tokens": 999, "output_tokens": 999},
            }
        )
        assert usage.source == "modelUsage"
        assert (usage.input_tokens, usage.output_tokens) == (10, 20)

    def test_no_usage_at_all_is_unmeasured(self) -> None:
        assert claude_attempt_usage({}).measured is False


class TestTraceAndResourcePlanesAgree:
    def test_same_file_yields_identical_token_totals(self, tmp_path: Path) -> None:
        tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
        tasks.mkdir(parents=True)
        log_path = tasks / "log.jsonl"
        log_path.write_text(FIXTURE.read_text())

        attempt = next(
            s for s in ClaudeAgentExtractor().extract(tmp_path, trace_id="t") if s.kind == "attempt"
        )

        log_file = LogFile(log_path, 0)
        log_file.read_new_events()
        resource_stats = log_file.usage_stats
        assert resource_stats is not None

        assert attempt.attributes["attempt.tokens_input"] == resource_stats.input_tokens
        assert attempt.attributes["attempt.tokens_output"] == resource_stats.output_tokens
        assert attempt.attributes["attempt.tokens_cached"] == resource_stats.cache_read_tokens

    def test_resource_plane_marks_claude_cost_as_estimated(self, tmp_path: Path) -> None:
        log_path = tmp_path / "log.jsonl"
        log_path.write_text(FIXTURE.read_text())
        log_file = LogFile(log_path, 0)
        log_file.read_new_events()
        assert log_file.usage_stats is not None
        assert log_file.usage_stats.cost_is_estimated is True


class TestCodexCacheIsNotDoubleCharged:
    def test_cached_input_is_subtracted_from_the_full_price_bucket(self) -> None:
        stats = sum_codex_usage(
            {
                "usage": {
                    "input_tokens": 1_000_000,
                    "cached_input_tokens": 900_000,
                    "output_tokens": 100_000,
                }
            }
        )
        stats.model = "gpt-5.5"

        assert stats.input_tokens == 100_000
        assert stats.cache_read_tokens == 900_000
        # Double-charging the cached portion produced $8.45; the disjoint split
        # is $3.95.
        assert compute_cost(stats, load_pricing()) == pytest.approx(3.95, abs=0.01)
