"""Round-trip tests for Phase 4 typed artifact models.

Covers PricingConfig, QaReport, QaSummary, InvocationRecord.
"""

from __future__ import annotations

from metaproc.logutil.usage import load_pricing, load_pricing_config
from metaproc.models.invocation import InvocationRecord
from metaproc.models.pricing import ModelPricing, PriceRates, PricingConfig, ProviderConfig
from metaproc.models.qa import (
    QaCheckCategory,
    QaCheckResult,
    QaReport,
    QaSummary,
    QaSummaryBlock,
    QaSummaryMatrixEntry,
    QaSummaryVariantTotal,
)

# ── PricingConfig ──────────────────────────────────────────────


class TestPricingConfig:
    def test_round_trip(self):
        config = PricingConfig(
            last_updated="2026-04-02",
            providers={
                "anthropic": ProviderConfig(
                    models={
                        "claude-opus-4-6": ModelPricing(
                            actual_price=PriceRates(
                                input_per_1m=5.0,
                                output_per_1m=25.0,
                                cache_read_per_1m=0.5,
                                cache_write_per_1m=6.25,
                            ),
                            cost_source="self-reported",
                        ),
                    }
                ),
            },
        )
        data = config.model_dump()
        restored = PricingConfig.model_validate(data)
        assert restored.last_updated == "2026-04-02"
        assert "claude-opus-4-6" in restored.providers["anthropic"].models
        rates = restored.providers["anthropic"].models["claude-opus-4-6"].actual_price
        assert rates.input_per_1m == 5.0
        assert rates.cache_write_per_1m == 6.25

    def test_flat_lookup(self):
        config = PricingConfig(
            providers={
                "anthropic": ProviderConfig(
                    models={
                        "claude-opus-4-6": ModelPricing(
                            actual_price=PriceRates(input_per_1m=5.0, output_per_1m=25.0),
                        ),
                    }
                ),
                "google": ProviderConfig(
                    models={
                        "gemini-pro": ModelPricing(
                            actual_price=PriceRates(input_per_1m=2.0, output_per_1m=12.0),
                        ),
                    }
                ),
            },
        )
        flat = config.flat_lookup()
        assert len(flat) == 2
        assert flat["claude-opus-4-6"].provider == "anthropic"
        assert flat["gemini-pro"].provider == "google"

    def test_parse_real_pricing_file(self):
        """Parse the actual pricing.md and validate all entries."""

        config = load_pricing_config()
        assert len(config.providers) >= 3  # anthropic, google, vertex-maas
        flat = config.flat_lookup()
        assert "claude-opus-4-6" in flat
        assert flat["claude-opus-4-6"].actual_price.input_per_1m == 5.0

    def test_list_price_optional(self):
        entry = ModelPricing(
            actual_price=PriceRates(input_per_1m=1.0, output_per_1m=2.0),
        )
        assert entry.list_price is None
        data = entry.model_dump()
        restored = ModelPricing.model_validate(data)
        assert restored.list_price is None

    def test_list_price_present(self):
        entry = ModelPricing(
            actual_price=PriceRates(input_per_1m=0.56, output_per_1m=1.68),
            list_price=PriceRates(input_per_1m=0.28, output_per_1m=0.42),
        )
        data = entry.model_dump()
        restored = ModelPricing.model_validate(data)
        assert restored.list_price is not None
        assert restored.list_price.input_per_1m == 0.28

    def test_load_pricing_backward_compat(self):
        """load_pricing() still returns dict[str, dict[str, Any]] shape."""

        pricing = load_pricing()
        assert isinstance(pricing, dict)
        assert "claude-opus-4-6" in pricing
        entry = pricing["claude-opus-4-6"]
        assert isinstance(entry["actual_price"], dict)
        assert entry["actual_price"]["input_per_1m"] == 5.0
        assert entry["provider"] == "anthropic"


# ── QaReport ───────────────────────────────────────────


class TestQaReport:
    def test_round_trip(self):
        envelope = QaReport(
            process="predict",
            item_id="AAPL",
            run_id="test-run",
            variant="claude-opus",
            timestamp="2026-04-09T00:00:00Z",
            summary=QaSummaryBlock(total_checks=5, passed=4, failed=1, verdict="fail"),
            operational=QaCheckCategory(
                total=2,
                passed=1,
                failed=1,
                checks=[
                    QaCheckResult(id="OP-1", status="pass", message="Process exited OK"),
                    QaCheckResult(id="OP-2", status="fail", message="Budget exceeded"),
                ],
            ),
        )
        data = envelope.model_dump()
        restored = QaReport.model_validate(data)
        assert restored.process == "predict"
        assert restored.item_id == "AAPL"
        assert restored.summary.verdict == "fail"
        assert len(restored.operational.checks) == 2
        assert restored.operational.checks[1].id == "OP-2"

    def test_minimal(self):
        """Minimal valid envelope (no checks)."""
        envelope = QaReport(
            process="mine",
            item_id="GOOG",
            run_id="r1",
            variant="v1",
            timestamp="2026-04-09T00:00:00Z",
        )
        assert envelope.summary.total_checks == 0
        assert envelope.operational.checks == []


# ── QaSummary ──────────────────────────────────────────


class TestQaSummary:
    def test_round_trip(self):
        envelope = QaSummary(
            process="predict",
            run_id="test-run",
            timestamp="2026-04-09T00:00:00Z",
            variants=["claude-opus", "gemini-pro"],
            matrix=[
                QaSummaryMatrixEntry(item_id="AAPL", variant="claude-opus", verdict="pass"),
                QaSummaryMatrixEntry(item_id="AAPL", variant="gemini-pro", verdict="fail"),
            ],
            totals=[
                QaSummaryVariantTotal(variant="claude-opus", verdict="pass", items=1, items_pass=1),
                QaSummaryVariantTotal(variant="gemini-pro", verdict="fail", items=1, items_fail=1),
            ],
        )
        data = envelope.model_dump()
        restored = QaSummary.model_validate(data)
        assert len(restored.variants) == 2
        assert len(restored.matrix) == 2
        assert restored.totals[1].items_fail == 1

    def test_empty_variants(self):
        envelope = QaSummary(
            process="mine",
            run_id="r1",
            timestamp="2026-04-09T00:00:00Z",
        )
        assert envelope.variants == []
        assert envelope.matrix == []
        assert envelope.totals == []


# ── InvocationRecord ───────────────────────────────────────────


class TestInvocationRecord:
    def test_round_trip_pending(self):
        record = InvocationRecord(
            run_id="test-run",
            step_id="predict",
            item={"EVENT_ID": "123", "TICKER": "AAPL"},
            state="pending",
        )
        data = record.model_dump()
        restored = InvocationRecord.model_validate(data)
        assert restored.state == "pending"
        assert restored.item["TICKER"] == "AAPL"
        assert restored.pid is None

    def test_round_trip_completed(self):
        record = InvocationRecord(
            run_id="test-run",
            step_id="predict",
            item={"EVENT_ID": "123", "TICKER": "AAPL"},
            pid=12345,
            command=["claude", "--model", "opus", "-p", "prompt.md"],
            adapter_type="claude-code-cli",
            model="opus",
            log_path=".logs/predict/AAPL.jsonl",
            started_at="2026-04-09T00:00:00Z",
            timeout_s=600,
            state="completed",
            completed_at="2026-04-09T00:05:30Z",
            exit_code=0,
            duration_s=330.5,
            cost_usd=0.42,
            total_tokens=50000,
            tool_uses=12,
        )
        data = record.model_dump()
        restored = InvocationRecord.model_validate(data)
        assert restored.state == "completed"
        assert restored.exit_code == 0
        assert restored.duration_s == 330.5
        assert restored.cost_usd == 0.42

    def test_round_trip_failed(self):
        record = InvocationRecord(
            run_id="test-run",
            step_id="mine",
            item={"EVENT_ID": "456"},
            state="failed",
            exit_code=1,
            error="Process exited with code 1",
            error_type="exit_nonzero",
        )
        data = record.model_dump()
        restored = InvocationRecord.model_validate(data)
        assert restored.state == "failed"
        assert restored.error_type == "exit_nonzero"

    def test_defaults(self):
        record = InvocationRecord(run_id="r1", step_id="s1")
        assert record.state == "pending"
        assert record.item == {}
        assert record.command == []
        assert record.pid is None
        assert record.validated is None
