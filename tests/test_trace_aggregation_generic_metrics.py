"""P2.1 (internal-reference) — generic sum:/avg:/max:/min: metrics + presets.

Pins the contract that `metaproc trace --rollup --metric sum:<attr>` works
for any numeric attribute path, and the named presets expand correctly.
"""

from __future__ import annotations

import pytest

from metaproc.commands.trace import TRACE_PRESETS
from metaproc.trace.aggregation import (
    aggregate,
    is_supported_metric,
    supported_metrics,
)
from metaproc.trace.schema import TraceEvent
from metaproc.trace.views import project_spans


def _attempt(adapter_type: str, step: str, cost: float, tokens_in: int) -> TraceEvent:
    return TraceEvent(
        trace_id="t",
        span_id=f"span-{adapter_type}-{step}-{cost}",
        name="attempt",
        kind="attempt",
        source=f"{adapter_type.split('-')[0]}-agent",
        ts_start="2026-05-26T00:00:00Z",
        status="ok",
        attributes={
            "adapter.type": adapter_type,
            "step.id": step,
            "attempt.cost_usd": cost,
            "attempt.tokens_input": tokens_in,
        },
    )


def test_named_metrics_still_supported() -> None:
    assert is_supported_metric("count")
    assert is_supported_metric("cost")
    assert is_supported_metric("duration_ms")
    assert "count" in supported_metrics()


def test_generic_sum_metric_supported() -> None:
    """sum:<attr> works for any numeric attribute path."""
    assert is_supported_metric("sum:attempt.cost_usd")
    assert is_supported_metric("sum:foo.bar.baz")


def test_generic_metric_prefixes_supported() -> None:
    for prefix in ("sum", "avg", "max", "min"):
        assert is_supported_metric(f"{prefix}:any.attr"), prefix


def test_unsupported_metric_rejected() -> None:
    assert not is_supported_metric("frobnicate")
    assert not is_supported_metric("median:foo")  # not in the canonical set


def test_sum_over_canonical_cost_attr() -> None:
    spans = [
        _attempt("claude-code-cli", "step-a", 0.01, 100),
        _attempt("claude-code-cli", "step-a", 0.02, 200),
        _attempt("claude-code-cli", "step-b", 0.05, 500),
    ]
    rows = aggregate(spans, group_keys=["step.id"], metrics=["sum:attempt.cost_usd"])
    by_step = {r["step.id"]: r["sum:attempt.cost_usd"] for r in rows}
    assert by_step["step-a"] == 0.03
    assert by_step["step-b"] == 0.05


def test_sum_and_avg_combine_in_one_rollup() -> None:
    spans = [
        _attempt("codex-cli", "step-a", 0.0, 1000),
        _attempt("codex-cli", "step-a", 0.0, 2000),
    ]
    rows = aggregate(
        spans,
        group_keys=["adapter.type"],
        metrics=["count", "sum:attempt.tokens_input", "avg:attempt.tokens_input"],
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["count"] == 2
    assert r["sum:attempt.tokens_input"] == 3000
    assert r["avg:attempt.tokens_input"] == 1500


def test_max_and_min_metrics() -> None:
    spans = [
        _attempt("gemini-cli", "x", 0.001, 5),
        _attempt("gemini-cli", "x", 0.999, 50),
        _attempt("gemini-cli", "x", 0.500, 25),
    ]
    rows = aggregate(
        spans,
        group_keys=["adapter.type"],
        metrics=["max:attempt.cost_usd", "min:attempt.cost_usd"],
    )
    assert rows[0]["max:attempt.cost_usd"] == 0.999
    assert rows[0]["min:attempt.cost_usd"] == 0.001


def test_unknown_metric_raises_value_error() -> None:

    spans = [_attempt("pi-cli", "x", 0, 0)]
    with pytest.raises(ValueError, match="unsupported metric"):
        aggregate(spans, group_keys=["adapter.type"], metrics=["frobnicate"])


# ── Projection ──


def test_project_spans_extracts_attribute_columns() -> None:

    spans = [
        TraceEvent(
            trace_id="t",
            span_id="a",
            name="WebSearch",
            kind="tool_call",
            source="claude-agent",
            ts_start="2026-05-26T00:00:00Z",
            status="ok",
            attributes={
                "adapter.type": "claude-code-cli",
                "step.id": "research-step",
                "item.key": "AAPL",
                "tool.input.query": "Apple earnings",
            },
        ),
    ]
    rows = project_spans(spans, ["adapter.type", "step.id", "item.key", "tool.input.query"])
    assert rows == [["claude-code-cli", "research-step", "AAPL", "Apple earnings"]]


def test_project_spans_top_level_fields() -> None:
    """Top-level fields (kind, source, status, name) are also projectable."""

    spans = [
        TraceEvent(
            trace_id="t",
            span_id="a",
            name="agent_attempt",
            kind="attempt",
            source="codex-agent",
            ts_start="2026-05-26T00:00:00Z",
            status="ok",
            attributes={"step.id": "x"},
        ),
    ]
    rows = project_spans(spans, ["kind", "source", "status", "step.id"])
    assert rows == [["attempt", "codex-agent", "ok", "x"]]


def test_project_spans_missing_attr_is_empty() -> None:

    spans = [
        TraceEvent(
            trace_id="t",
            span_id="a",
            name="x",
            kind="tool_call",
            source="claude-agent",
            ts_start="2026-05-26T00:00:00Z",
            status="ok",
            attributes={},
        ),
    ]
    rows = project_spans(spans, ["adapter.type", "step.id"])
    assert rows == [["", ""]]


# ── Presets ──


def test_presets_table_defined() -> None:

    assert "by-adapter-step-tool" in TRACE_PRESETS
    assert "cost-by-step" in TRACE_PRESETS
    assert "adapter.type" in TRACE_PRESETS["by-adapter-step-tool"]["group"]
    assert "sum:attempt.cost_usd" in TRACE_PRESETS["cost-by-step"]["metric"]
    # cost-by-step must restrict to attempt spans so we don't sum on tool_call
    # spans (which don't carry attempt.cost_usd).
    assert TRACE_PRESETS["cost-by-step"]["filter_kind"] == ["attempt"]
