"""Tests for metaproc.logutil.usage — usage and cost tracking."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.io import fmf_read
from metaproc.logutil.parsing import LogFile
from metaproc.logutil.usage import (
    UsageStats,
    _DualCostAccum,
    aggregate_usage,
    compute_cost,
    extract_gemini_usage,
    load_pricing,
    sum_codex_usage,
    sum_pi_usage,
    write_usage_report,
)
from metaproc.models.usage import _cost_view_dict
from metaproc.plugins.discovery import get_plugin_registry

_PRICING_DOC_PATH = Path(__file__).resolve().parents[1] / "src" / "metaproc" / "data" / "pricing.md"
_COMPARISON_TABLE_HEADER = (
    "| Model | Provider | Input | Norm | Output | Cache Read | Cache Write | Source |"
)
_NORM_BASE_INPUT_PER_1M = 0.28  # deepseek-chat direct-API input price
_TABLE_OMITTED_MODELS = {"gemini-3.1-pro-preview-customtools"}
# Derived from the central provider registry so adding a new provider in
# metaproc/config/providers.py is a one-place change. The pricing table
# does not include providers that have no rows in pricing.md (e.g. the
# pi-cli cloud-vendor names: vertex, azure, bedrock); the comparison-table
# build skips those naturally because they don't appear in the YAML.
from metaproc.config.providers import provider_labels as _provider_labels  # noqa: E402

_PROVIDER_LABELS = _provider_labels()
_MODEL_DISPLAY_NAMES = {
    "glm-5-maas": "glm-5",
    "kimi-k2-thinking-maas": "kimi-k2-thinking",
    "deepseek-v3.2-maas": "deepseek-v3.2",
    "qwen3-235b-a22b-instruct-2507-maas": "qwen3-235b",
    "qwen3-coder-480b-a35b-instruct-maas": "qwen3-coder-480b",
}


def _format_price_cell(value: float | None) -> str:
    if value is None:
        return "--"

    rendered = f"{value:.5f}".rstrip("0").rstrip(".")
    if "." not in rendered:
        rendered = f"{rendered}.00"
    elif len(rendered.rsplit(".", 1)[1]) == 1:
        rendered = f"{rendered}0"
    return f"${rendered}"


def _parse_markdown_link(markdown: str) -> tuple[str, str]:
    match = re.fullmatch(r"\[(?P<label>[^\]]+)\]\((?P<url>[^)]+)\)", markdown)
    if not match:
        msg = f"invalid markdown link: {markdown}"
        raise AssertionError(msg)
    return match.group("label"), match.group("url")


def _parse_comparison_table(content: str) -> list[dict[str, str]]:
    lines = content.splitlines()
    start = lines.index(_COMPARISON_TABLE_HEADER)
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(
            {
                "model": cells[0],
                "provider": cells[1],
                "input": cells[2],
                "norm": cells[3],
                "output": cells[4],
                "cache_read": cells[5],
                "cache_write": cells[6],
                "source": cells[7],
            }
        )
    return rows


def _build_expected_comparison_rows(meta: dict[str, Any]) -> list[dict[str, Any]]:
    sortable_rows: list[tuple[Any, int, dict[str, Any]]] = []
    insertion_order = 0

    for provider_name, provider_data in meta["providers"].items():
        for model_name, model_data in provider_data["models"].items():
            if model_name in _TABLE_OMITTED_MODELS:
                continue

            prices = model_data.get("list_price", model_data["actual_price"])
            source_url = model_data.get("list_source_url", model_data["source_url"])
            input_price = prices.get("input_per_1m")
            norm_cell = (
                f"{input_price / _NORM_BASE_INPUT_PER_1M:.2f}x" if input_price is not None else "--"
            )
            row = {
                "model": _MODEL_DISPLAY_NAMES.get(model_name, model_name),
                "provider": _PROVIDER_LABELS[provider_name],
                "input": _format_price_cell(input_price),
                "norm": norm_cell,
                "output": _format_price_cell(prices.get("output_per_1m")),
                "cache_read": _format_price_cell(prices.get("cache_read_per_1m")),
                "cache_write": _format_price_cell(prices.get("cache_write_per_1m")),
                "source": source_url,
            }
            sortable_rows.append((prices["output_per_1m"], insertion_order, row))
            insertion_order += 1

    sortable_rows.sort(key=lambda item: (-item[0], item[1]))
    return [row for _, _, row in sortable_rows]


# ── UsageStats ──────────────────────────────────────────────────


class TestUsageStats:
    def test_defaults(self) -> None:
        s = UsageStats()
        assert s.input_tokens == 0
        assert s.cost_usd == 0.0
        assert s.cost_is_estimated is False
        assert s.total_tokens == 0

    def test_iadd(self) -> None:
        a = UsageStats(input_tokens=100, output_tokens=50, cost_usd=1.0, steps=1)
        b = UsageStats(
            input_tokens=200,
            output_tokens=100,
            cost_usd=2.0,
            cost_is_estimated=True,
            steps=1,
        )
        a += b
        assert a.input_tokens == 300
        assert a.output_tokens == 150
        assert a.cost_usd == 3.0
        assert a.cost_is_estimated is True
        assert a.steps == 2

    def test_iadd_preserves_estimated_flag(self) -> None:
        a = UsageStats(cost_is_estimated=True)
        b = UsageStats(cost_is_estimated=False)
        a += b
        assert a.cost_is_estimated is True

    def test_iadd_carries_forward_model_and_provider(self) -> None:
        a = UsageStats(input_tokens=100)
        b = UsageStats(input_tokens=200, model="opus", provider="anthropic")
        a += b
        assert a.model == "opus"
        assert a.provider == "anthropic"
        # Once set, doesn't get overwritten by a later addition
        c = UsageStats(input_tokens=50, model="sonnet", provider="google")
        a += c
        assert a.model == "opus"
        assert a.provider == "anthropic"

    def test_total_tokens(self) -> None:
        s = UsageStats(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=200,
            cache_write_tokens=10,
        )
        assert s.total_tokens == 360


# ── Pricing ─────────────────────────────────────────────────────


class TestPricing:
    def test_load_pricing_from_frontmatter(self) -> None:
        """pricing.md YAML frontmatter is parsed correctly into flat model dict."""
        pricing = load_pricing()
        # Anthropic model present with correct structure
        assert "claude-opus-4-6" in pricing
        assert pricing["claude-opus-4-6"]["actual_price"]["input_per_1m"] == 5.0
        assert pricing["claude-opus-4-6"]["provider"] == "anthropic"
        # Google model present
        assert "gemini-3.1-pro-preview" in pricing
        assert pricing["gemini-3.1-pro-preview"]["actual_price"]["input_per_1m"] == 2.0
        assert pricing["gemini-3-flash-preview"]["provider"] == "google"
        # Vertex MaaS model present with public actual pricing
        assert "glm-5-maas" in pricing
        assert pricing["glm-5-maas"]["provider"] == "vertex-maas"
        assert pricing["glm-5-maas"]["actual_price"]["input_per_1m"] == 1.0
        assert "list_price" not in pricing["glm-5-maas"]
        # deepseek-v3.2-maas list_price tracks DeepSeek's direct-API rate.
        # As of 2026-05-23, the direct API rolled V3.2 into the V4-Flash alias
        # chain (deepseek-chat → v4-flash non-thinking), so list_price is
        # V4-Flash's $0.14 cache-miss input. See pricing.md note on v3.2-maas.
        assert pricing["deepseek-v3.2-maas"]["list_price"]["input_per_1m"] == 0.14

    def test_compute_cost_known_model(self) -> None:
        pricing = load_pricing()
        stats = UsageStats(
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_read_tokens=500_000,
            model="claude-opus-4-6",
        )
        cost = compute_cost(stats, pricing)
        # 1M * 5/1M + 100K * 25/1M + 500K * 0.5/1M = 5 + 2.5 + 0.25 = 7.75
        assert abs(cost - 7.75) < 0.001

    def test_compute_cost_unknown_model(self) -> None:
        pricing = load_pricing()
        stats = UsageStats(input_tokens=1000, model="unknown-model-xyz")
        cost = compute_cost(stats, pricing)
        assert cost == 0.0

    def test_compute_cost_org_prefixed_model(self) -> None:
        """Pi CLI reports models with org prefix like 'some-org/claude-sonnet-4-6'."""
        pricing = load_pricing()
        stats = UsageStats(
            input_tokens=1_000_000,
            output_tokens=100_000,
            model="some-org/claude-sonnet-4-6",
        )
        # Should find via basename stripping and compute a real cost.
        cost = compute_cost(stats, pricing)
        # 1M * 3/1M + 100K * 15/1M = 3 + 1.5 = 4.5
        assert abs(cost - 4.5) < 0.001

    def test_compute_cost_with_cache_write(self) -> None:
        pricing = load_pricing()
        stats = UsageStats(
            input_tokens=0,
            output_tokens=0,
            cache_write_tokens=1_000_000,
            model="claude-opus-4-6",
        )
        cost = compute_cost(stats, pricing)
        # 1M * 6.25/1M = 6.25
        assert abs(cost - 6.25) < 0.001

    def test_compute_cost_list_prices_vertex_maas(self) -> None:
        """List prices use vendor API rates when they differ from Vertex rates."""
        pricing = load_pricing()
        stats = UsageStats(
            input_tokens=1_000_000,
            output_tokens=100_000,
            model="deepseek-v3.2-maas",
        )
        actual = compute_cost(stats, pricing)
        # 1M * 0.56/1M + 100K * 1.68/1M = 0.56 + 0.168 = 0.728
        assert abs(actual - 0.728) < 0.001

        list_cost = compute_cost(stats, pricing, use_list_prices=True)
        # 1M * 0.14/1M + 100K * 0.28/1M = 0.14 + 0.028 = 0.168
        # (list_price updated 2026-05-23 to V4-Flash rates after the
        # deepseek-chat → v4-flash alias rollup retired V3.2 direct pricing.)
        assert abs(list_cost - 0.168) < 0.001

    def test_compute_cost_list_prices_fallback(self) -> None:
        """Models without list_* fields fall back to actual rates."""
        pricing = load_pricing()
        stats = UsageStats(
            input_tokens=1_000_000,
            output_tokens=100_000,
            model="claude-opus-4-6",
        )
        actual = compute_cost(stats, pricing)
        list_cost = compute_cost(stats, pricing, use_list_prices=True)
        # Anthropic has no list_* fields — list == actual
        assert abs(actual - list_cost) < 0.001

    def test_markdown_comparison_table_matches_frontmatter_exactly(self) -> None:
        content, meta = fmf_read(_PRICING_DOC_PATH)
        assert meta is not None

        actual_rows = _parse_comparison_table(content)
        expected_rows = _build_expected_comparison_rows(meta)

        assert len(actual_rows) == len(expected_rows)
        for actual_row, expected_row in zip(actual_rows, expected_rows, strict=True):
            assert actual_row["model"] == expected_row["model"]
            assert actual_row["provider"] == expected_row["provider"]
            assert actual_row["input"] == expected_row["input"]
            assert actual_row["output"] == expected_row["output"]
            assert actual_row["cache_read"] == expected_row["cache_read"]
            assert actual_row["cache_write"] == expected_row["cache_write"]
            _, source_url = _parse_markdown_link(actual_row["source"])
            assert source_url == expected_row["source"]

    def test_frontmatter_models_are_either_documented_or_explicitly_omitted(self) -> None:
        content, meta = fmf_read(_PRICING_DOC_PATH)
        assert meta is not None
        documented_models = {row["model"] for row in _parse_comparison_table(content)}

        expected_models = set()
        for provider_data in meta["providers"].values():
            for model_name in provider_data["models"]:
                if model_name in _TABLE_OMITTED_MODELS:
                    continue
                expected_models.add(_MODEL_DISPLAY_NAMES.get(model_name, model_name))

        assert documented_models == expected_models

    def test_last_updated_is_not_older_than_any_last_reviewed(self) -> None:
        _, meta = fmf_read(_PRICING_DOC_PATH)
        assert meta is not None

        last_updated = date.fromisoformat(meta["last_updated"])
        model_review_dates = [
            date.fromisoformat(model_data["last_reviewed"])
            for provider_data in meta["providers"].values()
            for model_data in provider_data["models"].values()
        ]

        assert last_updated >= max(model_review_dates)


# ── Pi CLI usage extraction ─────────────────────────────────────


class TestSumPiUsage:
    def test_basic_extraction(self) -> None:
        agent_end = {
            "type": "agent_end",
            "messages": [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "provider": "anthropic",
                    "usage": {
                        "input": 3103,
                        "output": 96,
                        "cacheRead": 0,
                        "cacheWrite": 0,
                        "totalTokens": 3199,
                        "cost": {"total": 0.017915},
                    },
                },
                {"role": "user", "content": "thanks"},
                {
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "provider": "anthropic",
                    "usage": {
                        "input": 1,
                        "output": 287,
                        "cacheRead": 0,
                        "cacheWrite": 5676,
                        "totalTokens": 5964,
                        "cost": {"total": 0.042655},
                    },
                },
            ],
        }
        stats = sum_pi_usage(agent_end)
        assert stats.input_tokens == 3104
        assert stats.output_tokens == 383
        assert stats.cache_write_tokens == 5676
        assert abs(stats.cost_usd - 0.06057) < 0.001
        assert stats.model == "claude-opus-4-6"
        assert stats.provider == "anthropic"

    def test_empty_messages(self) -> None:
        stats = sum_pi_usage({"type": "agent_end", "messages": []})
        assert stats.input_tokens == 0
        assert stats.cost_usd == 0.0

    def test_no_messages_key(self) -> None:
        stats = sum_pi_usage({"type": "agent_end"})
        assert stats.input_tokens == 0


# ── Gemini CLI usage extraction ─────────────────────────────────


class TestExtractGeminiUsage:
    def test_per_model_breakdown(self) -> None:
        stats_dict = {
            "total_tokens": 1274460,
            "input_tokens": 1244336,
            "output_tokens": 19105,
            "cached": 1055731,
            "tool_calls": 33,
            "models": {
                "gemini-3.1-pro-preview-customtools": {
                    "total_tokens": 1238040,
                    "input_tokens": 1208999,
                    "output_tokens": 18751,
                    "cached": 1055731,
                },
                "gemini-3-flash-preview": {
                    "total_tokens": 36420,
                    "input_tokens": 35337,
                    "output_tokens": 354,
                    "cached": 0,
                },
            },
        }
        result = extract_gemini_usage(stats_dict)
        assert len(result) == 2
        pro = next(r for r in result if r.model == "gemini-3.1-pro-preview-customtools")
        assert pro.input_tokens == 1208999
        assert pro.output_tokens == 18751
        assert pro.cache_read_tokens == 1055731
        assert pro.provider == "google"

    def test_aggregate_fallback(self) -> None:
        stats_dict = {
            "input_tokens": 5000,
            "output_tokens": 1000,
            "cached": 200,
            "tool_calls": 5,
        }
        result = extract_gemini_usage(stats_dict)
        assert len(result) == 1
        assert result[0].input_tokens == 5000
        assert result[0].cache_read_tokens == 200
        assert result[0].tool_calls == 5


# ── Codex CLI usage extraction ──────────────────────────────────


class TestSumCodexUsage:
    """codex-cli 0.124.0 turn.completed envelope — usage inline."""

    def test_extracts_inline_usage(self) -> None:
        event = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 6740,
                "cached_input_tokens": 1024,
                "output_tokens": 123,
            },
        }
        stats = sum_codex_usage(event)
        assert stats.input_tokens == 6740
        assert stats.cache_read_tokens == 1024
        assert stats.output_tokens == 123
        assert stats.provider == "openai"

    def test_missing_usage_returns_zeros(self) -> None:
        stats = sum_codex_usage({"type": "turn.completed"})
        assert stats.input_tokens == 0
        assert stats.output_tokens == 0
        assert stats.cache_read_tokens == 0
        assert stats.provider == "openai"

    def test_usage_cost_roll_up(self) -> None:
        """End-to-end: codex usage + pricing.md rates produce expected cost."""
        event = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1_000_000,  # 1M input tokens
                "cached_input_tokens": 0,
                "output_tokens": 1_000_000,  # 1M output tokens
            },
        }
        stats = sum_codex_usage(event)
        stats.model = "gpt-5.5"
        pricing = load_pricing()
        cost = compute_cost(stats, pricing)
        # gpt-5.5: $5.00 input / $30.00 output per 1M. Expect $35.00.
        assert cost == pytest.approx(35.00, abs=0.01)


# ── LogFile.usage_stats integration ─────────────────────────────


class TestLogFileUsageStats:
    def test_pi_logfile_populates_usage_stats(self, tmp_path: Path) -> None:
        log_path = tmp_path / "step_ctx.jsonl"
        lines = [
            json.dumps({"type": "session", "version": 3, "id": "test", "cwd": "/tmp"}),
            json.dumps({"type": "agent_start", "model": "claude-opus-4-6"}),
            json.dumps(
                {
                    "type": "agent_end",
                    "messages": [
                        {"role": "user", "content": "hi"},
                        {
                            "role": "assistant",
                            "model": "claude-opus-4-6",
                            "provider": "anthropic",
                            "usage": {
                                "input": 100,
                                "output": 50,
                                "cacheRead": 200,
                                "cacheWrite": 10,
                                "cost": {"total": 0.05},
                            },
                        },
                    ],
                }
            ),
        ]
        log_path.write_text("\n".join(lines) + "\n")

        lf = LogFile(log_path, 0)
        lf.read_new_events()

        assert lf.usage_stats is not None
        assert lf.usage_stats.input_tokens == 100
        assert lf.usage_stats.output_tokens == 50
        assert lf.usage_stats.cache_read_tokens == 200
        assert lf.usage_stats.cache_write_tokens == 10
        assert abs(lf.usage_stats.cost_usd - 0.05) < 0.001
        assert lf.usage_stats.model == "claude-opus-4-6"
        assert lf.usage_stats.steps == 1

    def test_claude_logfile_populates_usage_stats(self, tmp_path: Path) -> None:
        log_path = tmp_path / "step_ctx.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "model": "claude-sonnet-4-6",
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "is_error": False,
                    "cost_usd": 0.045,
                    "duration_ms": 5000,
                    "usage": {
                        "input_tokens": 5000,
                        "output_tokens": 300,
                        "cache_read_input_tokens": 60000,
                        "cache_creation_input_tokens": 1000,
                    },
                }
            ),
        ]
        log_path.write_text("\n".join(lines) + "\n")

        lf = LogFile(log_path, 0)
        lf.read_new_events()

        assert lf.usage_stats is not None
        assert lf.usage_stats.input_tokens == 5000
        assert lf.usage_stats.output_tokens == 300
        assert lf.usage_stats.cache_read_tokens == 60000
        assert lf.usage_stats.cache_write_tokens == 1000
        assert abs(lf.usage_stats.cost_usd - 0.045) < 0.001
        assert lf.usage_stats.provider == "anthropic"
        assert lf.usage_stats.steps == 1

    def test_gemini_logfile_populates_usage_stats(self, tmp_path: Path) -> None:
        log_path = tmp_path / "step_ctx.jsonl"
        lines = [
            "YOLO mode enabled",
            json.dumps(
                {
                    "type": "result",
                    "status": "success",
                    "stats": {
                        "input_tokens": 10000,
                        "output_tokens": 500,
                        "cached": 8000,
                        "duration_ms": 30000,
                        "tool_calls": 5,
                        "models": {
                            "gemini-3.1-pro-preview-customtools": {
                                "input_tokens": 10000,
                                "output_tokens": 500,
                                "cached": 8000,
                            },
                        },
                    },
                }
            ),
        ]
        log_path.write_text("\n".join(lines) + "\n")

        lf = LogFile(log_path, 0)
        lf.read_new_events()

        assert lf.usage_stats is not None
        assert lf.usage_stats.input_tokens == 10000
        assert lf.usage_stats.output_tokens == 500
        assert lf.usage_stats.cache_read_tokens == 8000
        assert lf.usage_stats.cost_is_estimated is True
        assert lf.usage_stats.steps == 1


# ── Aggregation ─────────────────────────────────────────────────


def _make_pi_log(tmp_path: Path, variant: str, filename: str, agent_end: dict[str, Any]) -> LogFile:
    """Helper to create a Pi CLI log file and parse it."""
    variant_dir = tmp_path / variant / ".logs"
    variant_dir.mkdir(parents=True, exist_ok=True)
    log_path = variant_dir / filename
    lines = [
        json.dumps({"type": "session", "version": 3, "id": "test", "cwd": "/tmp"}),
        json.dumps({"type": "agent_start", "model": "claude-opus-4-6"}),
        json.dumps(agent_end),
    ]
    log_path.write_text("\n".join(lines) + "\n")
    lf = LogFile(log_path, 0)
    lf.read_new_events()
    return lf


class _SyntheticToolProfileSource:
    name = "synthetic"

    def matches(self, path: Path) -> bool:
        return path.name == "tool-events.jsonl"

    def aggregate(
        self,
        paths: Sequence[Path],
        *,
        variant_fn: Callable[[Path], str],
    ) -> dict[str, ToolRunProfile]:
        variant = variant_fn(paths[0])
        return {
            variant: ToolRunProfile(
                variant=variant,
                records=1,
                per_tool={
                    "lookup": ToolCallStats(
                        tool_name="lookup",
                        calls=4,
                        ok=3,
                        failures={"tool_error": 1},
                        duration_s=4.0,
                    )
                },
                total_configs=1,
                cutoff_disc_pct=0.0,
            )
        }


class TestAggregateUsage:
    def test_aggregates_across_variants(self, tmp_path: Path) -> None:
        lf1 = _make_pi_log(
            tmp_path,
            "pi-cli-opus",
            "step_t1.jsonl",
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "provider": "anthropic",
                        "usage": {
                            "input": 1000,
                            "output": 100,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "cost": {"total": 0.05},
                        },
                    }
                ],
            },
        )
        lf2 = _make_pi_log(
            tmp_path,
            "pi-cli-opus",
            "step_t2.jsonl",
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "provider": "anthropic",
                        "usage": {
                            "input": 2000,
                            "output": 200,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "cost": {"total": 0.10},
                        },
                    }
                ],
            },
        )

        report = aggregate_usage([lf1, lf2])

        assert report.totals.input_tokens == 3000
        assert report.totals.output_tokens == 300
        assert report.totals.cost.actual.cost_usd is not None
        assert abs(report.totals.cost.actual.cost_usd - 0.15) < 0.001
        assert "pi-cli-opus" in report.by_variant
        assert "claude-opus-4-6" in report.by_model
        assert "anthropic" in report.by_provider

    def test_unknown_model_warning(self, tmp_path: Path) -> None:
        lf = _make_pi_log(
            tmp_path,
            "pi-unknown",
            "step_t1.jsonl",
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "unknown-model-xyz",
                        "provider": "unknown",
                        "usage": {
                            "input": 100,
                            "output": 10,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "cost": {"total": 0.0},
                        },
                    }
                ],
            },
        )

        report = aggregate_usage([lf])
        assert any("unknown-model-xyz" in w for w in report.warnings)

    def test_dual_pricing_actual_vs_list(self, tmp_path: Path) -> None:
        """Aggregation returns both actual (Vertex) and list (vendor) costs for MaaS."""
        lf = _make_pi_log(
            tmp_path,
            "pi-cli-deepseek",
            "step_t1.jsonl",
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "deepseek-ai/deepseek-v3.2-maas",
                        "provider": "",
                        "usage": {
                            "input": 1_000_000,
                            "output": 100_000,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "cost": {"total": 0.0},
                        },
                    }
                ],
            },
        )

        report = aggregate_usage([lf])

        # Actual cost: Vertex's published pay-as-you-go pricing.
        assert report.totals.cost.actual.cost_usd is not None
        assert abs(report.totals.cost.actual.cost_usd - 0.728) < 0.001

        # List cost: vendor API rates (V4-Flash post-2026-05-23 alias rollup).
        assert report.totals.cost.list.cost_usd is not None
        assert abs(report.totals.cost.list.cost_usd - 0.168) < 0.001

        # Data appears at every aggregation level.
        assert "pi-cli-deepseek" in report.by_variant
        assert "deepseek-ai/deepseek-v3.2-maas" in report.by_model
        assert "vertex-maas" in report.by_provider


# ── Report writing ──────────────────────────────────────────────


class TestWriteUsageReport:
    def test_writes_valid_frontmatter(self, tmp_path: Path) -> None:
        lf = _make_pi_log(
            tmp_path,
            "pi-cli-opus",
            "step_t1.jsonl",
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "provider": "anthropic",
                        "usage": {
                            "input": 5000,
                            "output": 500,
                            "cacheRead": 1000,
                            "cacheWrite": 0,
                            "cost": {"total": 0.12},
                        },
                    }
                ],
            },
        )

        out = tmp_path / "usage.md"
        write_usage_report(out, "test-run", "predict", [lf])

        assert out.exists()
        content, meta = fmf_read(out)
        assert meta is not None
        assert meta["run_id"] == "test-run"
        assert meta["phase"] == "predict"
        assert "totals" in meta
        assert meta["totals"]["input_tokens"] == 5000
        assert meta["totals"]["cost"]["actual"]["cost_usd"] is not None
        assert meta["totals"]["cost"]["list"] is not None
        assert "# Usage Report" in content
        assert "Actual Cost" in content
        assert "List Cost" in content

    def test_report_shows_dual_costs(self, tmp_path: Path) -> None:
        """Report includes both actual and list cost columns for Vertex MaaS models."""
        lf = _make_pi_log(
            tmp_path,
            "pi-cli-deepseek",
            "step_t1.jsonl",
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "deepseek-ai/deepseek-v3.2-maas",
                        "provider": "",
                        "usage": {
                            "input": 1_000_000,
                            "output": 100_000,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "cost": {"total": 0.0},
                        },
                    }
                ],
            },
        )

        out = tmp_path / "usage.md"
        write_usage_report(out, "test-run", "predict", [lf])

        content, meta = fmf_read(out)
        assert meta is not None
        # Actual cost ~$0.73, list cost ~$0.17 (post-2026-05-23 V4-Flash alias).
        actual_cost = meta["totals"]["cost"]["actual"]["cost_usd"]
        list_cost = meta["totals"]["cost"]["list"]["cost_usd"]
        assert abs(actual_cost - 0.728) < 0.001
        assert abs(list_cost - 0.168) < 0.001
        # Prose summary shows both
        assert "Total actual cost: **$0.73**" in content
        assert "Total list cost: **$0.17**" in content

    def test_writes_profiles_from_registered_tool_event_source(self, tmp_path: Path) -> None:
        lf = _make_pi_log(
            tmp_path,
            "synthetic",
            "step_t1.jsonl",
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "provider": "anthropic",
                        "usage": {"input": 1000, "output": 100, "cost": {"total": 0.01}},
                    }
                ],
            },
        )

        session_dir = tmp_path / "synthetic"
        session_dir.mkdir(parents=True, exist_ok=True)
        events_path = session_dir / "tool-events.jsonl"
        events_path.write_text("{}\n")

        out = tmp_path / "usage.md"
        registry = get_plugin_registry()
        original_sources = registry.tool_profile_sources.copy()
        try:
            registry.register_tool_profile_source(_SyntheticToolProfileSource())
            write_usage_report(
                out,
                "test-run",
                "phase",
                [lf],
                tool_event_files=[events_path],
                phase_dir=tmp_path,
            )
        finally:
            registry.tool_profile_sources = original_sources

        content, meta = fmf_read(out)
        assert meta is not None
        profiles = meta["tool_profiles"]
        assert "synthetic" in profiles
        profile = profiles["synthetic"]
        assert profile["records"] == 1
        assert profile["total_configs"] == 1
        assert profile["per_tool"]["lookup"]["calls"] == 4
        assert profile["per_tool"]["lookup"]["ok"] == 3
        assert profile["per_tool"]["lookup"]["failures"] == {"tool_error": 1}
        assert "## Tool-use by Variant" in content
        assert "| Variant | Records | Tool calls | Tool fail%" in content
        assert "| synthetic | 1 | 4 | 25.0% | 0.0% | off | 0.00 |" in content

    def test_rate_limit_events_bin_by_provider_adapter_variant(self, tmp_path: Path) -> None:
        # Claude-code log with one blocked + one allowed rate_limit_event.
        log_path = tmp_path / "pi-cli-opus" / ".logs" / "step_t1.jsonl"
        log_path.parent.mkdir(parents=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "s", "model": "opus"})
            + "\n"
            + json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "blocked", "rateLimitType": "five_hour"},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "blocked", "rateLimitType": "five_hour"},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"},
                }
            )
            + "\n"
        )
        lf = LogFile(log_path, 0)
        lf.read_new_events()
        report = aggregate_usage([lf], pricing={})
        assert len(report.rate_limit_stats) == 1
        rls = report.rate_limit_stats[0]
        assert rls.provider == "anthropic"
        assert rls.adapter == "claude"
        assert rls.variant == "pi-cli-opus"
        assert rls.count == 2  # allowed excluded

    def test_tool_event_files_without_phase_dir_raises(self, tmp_path: Path) -> None:
        lf = _make_pi_log(
            tmp_path,
            "v",
            "s.jsonl",
            {"type": "agent_end", "messages": []},
        )
        session_dir = tmp_path / "v" / "e" / ".logs"
        session_dir.mkdir(parents=True)
        (session_dir / "resource-events.jsonl").write_text("")

        with pytest.raises(ValueError, match="phase_dir"):
            write_usage_report(
                tmp_path / "usage.md",
                "r",
                "p",
                [lf],
                tool_event_files=[session_dir / "resource-events.jsonl"],
            )


# ── Usage model tests ────────────────────────────────────────────


from metaproc.models.usage import (
    CostPair,
    CostView,
    ProviderRateLimitStats,
    ToolCallStats,
    ToolRunProfile,
    UsageBucket,
    UsageReport,
    bucket_to_dict,
    usage_report_to_frontmatter,
)


class TestUsageModels:
    def test_cost_view_defaults(self) -> None:
        cv = CostView()
        assert cv.cost_usd is None
        assert cv.is_estimated is False

    def test_cost_pair_defaults(self) -> None:
        cp = CostPair()
        assert cp.actual.cost_usd is None
        assert cp.list.cost_usd is None

    def test_bucket_to_dict_omits_zeros(self) -> None:
        bucket = UsageBucket(
            input_tokens=1000,
            steps=3,
            model="test-model",
            cost=CostPair(
                actual=CostView(cost_usd=1.5),
                list=CostView(cost_usd=2.0, is_estimated=True),
            ),
        )
        d = bucket_to_dict(bucket)
        assert d["input_tokens"] == 1000
        assert "output_tokens" not in d
        assert "cache_read_tokens" not in d
        assert "duration_s" not in d
        cost = d["cost"]
        assert isinstance(cost, dict)
        assert cost["actual"] == {"cost_usd": 1.5}
        assert cost["list"] == {"cost_usd": 2.0, "is_estimated": True}

    def test_bucket_to_dict_excludes_timing_when_requested(self) -> None:
        bucket = UsageBucket(duration_s=100.0, tool_calls=5, steps=1)
        d = bucket_to_dict(bucket, include_timing=False)
        assert "duration_s" not in d
        assert "tool_calls" not in d

    def test_cost_usd_rounds_to_3_decimals(self) -> None:
        cv = CostView(cost_usd=1.23456789)

        d = _cost_view_dict(cv)
        assert d["cost_usd"] == 1.235

    def test_usage_report_round_trip(self) -> None:
        report = UsageReport(
            run_id="test-run",
            phase="predict",
            generated="2026-01-01T00:00:00Z",
            totals=UsageBucket(
                input_tokens=1000,
                output_tokens=500,
                steps=2,
                cost=CostPair(
                    actual=CostView(cost_usd=1.5),
                    list=CostView(cost_usd=2.0, is_estimated=True),
                ),
            ),
            by_variant={"v1": UsageBucket(steps=1)},
            warnings=["test warning"],
        )
        fm = usage_report_to_frontmatter(report)
        parsed = UsageReport.model_validate(fm)
        assert parsed.run_id == "test-run"
        assert parsed.totals.input_tokens == 1000
        # cost_usd round-trips through 3-decimal rounding.
        assert parsed.totals.cost.actual.cost_usd == 1.5
        assert parsed.totals.cost.list.is_estimated is True
        assert "v1" in parsed.by_variant
        assert parsed.warnings == ["test warning"]


class TestToolUseModels:
    """New models for tool-use telemetry (internal-reference / plan spec tool4)."""

    def test_tool_call_stats_defaults(self) -> None:
        stats = ToolCallStats(tool_name="filtered_web_search")
        assert stats.tool_name == "filtered_web_search"
        assert stats.calls == 0
        assert stats.ok == 0
        assert stats.failures == {}
        assert stats.duration_s == 0.0

    def test_tool_call_stats_accepts_failure_kinds(self) -> None:
        stats = ToolCallStats(
            tool_name="prices_historical",
            calls=10,
            ok=8,
            failures={"tool_error": 1, "tool_timeout": 1},
            duration_s=123.4,
        )
        assert stats.calls == 10
        assert stats.ok + sum(stats.failures.values()) == stats.calls

    def test_tool_run_profile_defaults(self) -> None:
        profile = ToolRunProfile(variant="pi-glm-5", records=0)
        assert profile.variant == "pi-glm-5"
        assert profile.records == 0
        assert profile.per_tool == {}
        assert profile.cutoff_disc_pct is None
        assert profile.live_mode_configs == 0
        assert profile.total_configs == 0

    def test_tool_run_profile_with_per_tool(self) -> None:
        profile = ToolRunProfile(
            variant="pi-deepseek-v3.2",
            records=15,
            per_tool={
                "earnings_reports": ToolCallStats(tool_name="earnings_reports", calls=30, ok=30)
            },
            total_configs=15,
            live_mode_configs=0,
            cutoff_disc_pct=100.0,
        )
        assert profile.per_tool["earnings_reports"].calls == 30
        assert profile.cutoff_disc_pct == 100.0

    def test_provider_rate_limit_stats_defaults(self) -> None:
        prls = ProviderRateLimitStats(provider="vertex-maas", adapter="pi", variant="pi-glm-5")
        assert prls.provider == "vertex-maas"
        assert prls.adapter == "pi"
        assert prls.variant == "pi-glm-5"
        assert prls.count == 0

    def test_usage_report_round_trip_with_tool_profiles(self) -> None:
        report = UsageReport(
            run_id="test-run",
            phase="mine",
            generated="2026-04-20T00:00:00Z",
            tool_profiles={
                "pi-glm-5": ToolRunProfile(
                    variant="pi-glm-5",
                    records=2,
                    per_tool={
                        "filtered_web_search": ToolCallStats(
                            tool_name="filtered_web_search",
                            calls=4,
                            ok=3,
                            failures={"tool_error": 1},
                            duration_s=200.0,
                        ),
                    },
                    total_configs=2,
                    live_mode_configs=0,
                    cutoff_disc_pct=100.0,
                )
            },
            rate_limit_stats=[
                ProviderRateLimitStats(
                    provider="vertex-maas", adapter="pi", variant="pi-glm-5", count=3
                ),
            ],
        )
        fm = usage_report_to_frontmatter(report)
        parsed = UsageReport.model_validate(fm)
        tp = parsed.tool_profiles["pi-glm-5"]
        assert tp.records == 2
        assert tp.per_tool["filtered_web_search"].calls == 4
        assert tp.per_tool["filtered_web_search"].failures == {"tool_error": 1}
        assert tp.cutoff_disc_pct == 100.0
        assert len(parsed.rate_limit_stats) == 1
        assert parsed.rate_limit_stats[0].count == 3

    def test_tool_run_profile_native_web_search_round_trip(self) -> None:
        report = UsageReport(
            run_id="r",
            phase="mine",
            generated="2026-04-20T00:00:00Z",
            tool_profiles={
                "pi-gemini-3.1-pro": ToolRunProfile(
                    variant="pi-gemini-3.1-pro",
                    records=3,
                    total_configs=3,
                    native_web_search_configs=3,
                ),
                "pi-glm-5": ToolRunProfile(
                    variant="pi-glm-5",
                    records=1,
                    total_configs=1,
                    native_web_search_configs=0,
                ),
            },
        )
        fm = usage_report_to_frontmatter(report)
        tool_profiles = cast(dict[str, dict[str, object]], fm["tool_profiles"])
        grounded = tool_profiles["pi-gemini-3.1-pro"]
        assert grounded["native_web_search_configs"] == 3
        # Zero value elided (matches live_mode_configs convention).
        plain = tool_profiles["pi-glm-5"]
        assert "native_web_search_configs" not in plain
        parsed = UsageReport.model_validate(fm)
        assert parsed.tool_profiles["pi-gemini-3.1-pro"].native_web_search_configs == 3
        assert parsed.tool_profiles["pi-glm-5"].native_web_search_configs == 0

    def test_usage_report_reads_without_tool_fields_backward_compatible(self) -> None:
        report = UsageReport(
            run_id="legacy-run",
            phase="predict",
            generated="2026-01-01T00:00:00Z",
        )
        fm = usage_report_to_frontmatter(report)
        parsed = UsageReport.model_validate(fm)
        assert parsed.tool_profiles == {}
        assert parsed.rate_limit_stats == []


class TestDualCostAccumModelAttribution:
    """Variant accumulators should attribute the dominant model, not first-seen."""

    def test_dominant_model_wins(self, tmp_path: Path) -> None:

        accum = _DualCostAccum()
        # First entry: small summary step with glm-5
        accum.add(
            UsageStats(input_tokens=100, output_tokens=50, model="glm-5", provider="vertex-maas"),
            actual=0.0,
            actual_estimated=True,
            list_=0.0,
            list_estimated=True,
        )
        # Second entry: large generation step with deepseek
        accum.add(
            UsageStats(
                input_tokens=5000, output_tokens=2000, model="deepseek-v3.2", provider="vertex-maas"
            ),
            actual=0.0,
            actual_estimated=True,
            list_=0.0,
            list_estimated=True,
        )
        bucket = accum.to_bucket()
        assert bucket.model == "deepseek-v3.2"
        assert bucket.provider == "vertex-maas"

    def test_single_model(self, tmp_path: Path) -> None:

        accum = _DualCostAccum()
        accum.add(
            UsageStats(
                input_tokens=1000, output_tokens=500, model="deepseek-v3.2", provider="vertex-maas"
            ),
            actual=0.0,
            actual_estimated=True,
            list_=0.0,
            list_estimated=True,
        )
        bucket = accum.to_bucket()
        assert bucket.model == "deepseek-v3.2"


class TestWriteUsageArenaToolsDiscovery:
    """Regression for the post-Arena-CLI-cutover write-usage discovery glob.

    The C1+C2 cutover (commit 351c4ea95) moved the Arena ResourceEvent JSONL
    file from per-ticker layout (`<variant>/<ticker>/.logs/tools/arena/...`)
    to a single phase-level flat file (`<phase>/.logs/tools/arena/...`).
    The write-usage CLI's default discovery glob was not updated, so it
    silently reported '0 with usage data' for every post-cutover run dir
    until the operator passed --arena-tools-glob explicitly. Surfaced
    2026-05-21 mid-AMC-resume validation pass.
    """

    def _write_event(self, events_path: Path) -> None:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            json.dumps(
                {
                    "event": "tool_call",
                    "ts": "2026-05-21T08:00:00.000Z",
                    "hierarchy": {
                        "run_id": "test-run",
                        "step_node_id": "analysis-research",
                        "item_key": "AAA",
                        "tool_name": "company_fundamentals_bundle",
                    },
                    "metrics": {
                        "tool_calls": 1,
                        "tool_failures": 0,
                        "tool_exec_s": 3.0,
                    },
                    "taxonomy": {
                        "tool_path": [
                            "execution",
                            "tool",
                            "arena",
                            "bundle",
                            "company_fundamentals_bundle",
                        ],
                        "time_kind_path": ["time", "tool_exec"],
                        "policy_path": ["policy", "1"],
                        "extra_paths": {"mode": ["backtest"]},
                    },
                    "source": {
                        "kind": "arena_cli",
                        "path": str(events_path),
                    },
                    "failure_kind": None,
                }
            )
            + "\n"
        )

    def test_default_glob_finds_flat_phase_level_path(self, tmp_path: Path) -> None:
        """Post-cutover canonical layout: <phase>/.logs/tools/arena/resource-events.jsonl"""

        phase_dir = tmp_path / "analysis-research"
        phase_dir.mkdir()
        events_path = phase_dir / ".logs" / "tools" / "arena" / "resource-events.jsonl"
        self._write_event(events_path)
        # Also drop a non-arena .jsonl so write-usage doesn't bail early on
        # "No .jsonl files found".
        (phase_dir / "other.jsonl").write_text("")

        result = CliRunner().invoke(app, ["write-usage", str(phase_dir)])
        assert result.exit_code == 0, result.output
        assert "Found 1 external ResourceEvent sessions" in result.output, (
            "default glob must match the post-cutover flat path "
            f".logs/tools/arena/resource-events.jsonl. Output: {result.output}"
        )

    def test_default_glob_falls_back_to_legacy_per_ticker_layout(self, tmp_path: Path) -> None:
        """Pre-cutover backward-compat: per-ticker layout still discovered."""

        phase_dir = tmp_path / "analysis-research"
        phase_dir.mkdir()
        # Legacy per-ticker path: <variant>/<ticker>/.logs/tools/arena/...
        events_path = (
            phase_dir
            / "claude-opus"
            / "AAA"
            / ".logs"
            / "tools"
            / "arena"
            / "resource-events.jsonl"
        )
        self._write_event(events_path)
        (phase_dir / "other.jsonl").write_text("")

        result = CliRunner().invoke(app, ["write-usage", str(phase_dir)])
        assert result.exit_code == 0, result.output
        assert "Found 1 external ResourceEvent sessions" in result.output, (
            "default glob must fall back to the legacy per-ticker layout "
            f"when the flat path is absent. Output: {result.output}"
        )
