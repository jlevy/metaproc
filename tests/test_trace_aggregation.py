"""Unit tests for the rollup aggregation primitive (C3)."""

from __future__ import annotations

from typing import Any

import pytest

from metaproc.trace.aggregation import aggregate, format_rollup_table
from metaproc.trace.schema import TraceEvent


def _span(**overrides: Any) -> TraceEvent:
    base: dict[str, Any] = {
        "trace_id": "t",
        "span_id": "x",
        "name": "n",
        "kind": "tool_call",
        "source": "claude-agent",
        "ts_start": "2026-05-12T00:00:00Z",
    }
    base.update(overrides)
    return TraceEvent(**base)  # type: ignore[arg-type]


def test_aggregate_count_by_kind():
    spans = [
        _span(span_id="a", kind="tool_call"),
        _span(span_id="b", kind="tool_call"),
        _span(span_id="c", kind="subprocess"),
    ]
    rows = aggregate(spans, group_keys=["kind"], metrics=["count"])
    assert rows == [
        {"kind": "subprocess", "count": 1},
        {"kind": "tool_call", "count": 2},
    ]


def test_aggregate_cost_sum_by_provider_attribute():
    spans = [
        _span(span_id="a", kind="provider_call", attributes={"provider": "exa", "cost.usd": 0.10}),
        _span(span_id="b", kind="provider_call", attributes={"provider": "exa", "cost.usd": 0.05}),
        _span(
            span_id="c", kind="provider_call", attributes={"provider": "gemini", "cost.usd": 0.20}
        ),
        _span(
            span_id="d",
            kind="provider_call",
            attributes={"provider": "perplexity", "cost.usd": 0.0},
        ),
    ]
    rows = aggregate(spans, group_keys=["provider"], metrics=["cost", "count"])
    by_provider = {r["provider"]: r for r in rows}
    assert by_provider["exa"]["cost"] == pytest.approx(0.15)
    assert by_provider["exa"]["count"] == 2
    assert by_provider["gemini"]["cost"] == pytest.approx(0.20)
    assert by_provider["perplexity"]["cost"] == 0


def test_aggregate_duration_ms_and_avg():
    spans = [
        _span(span_id="a", duration_ms=100.0),
        _span(span_id="b", duration_ms=300.0),
        _span(span_id="c", duration_ms=None),  # ignored from avg
    ]
    rows = aggregate(spans, group_keys=["kind"], metrics=["duration_ms", "avg_duration_ms"])
    assert rows[0]["duration_ms"] == pytest.approx(400.0)
    assert rows[0]["avg_duration_ms"] == pytest.approx(200.0)


def test_aggregate_groups_by_multiple_keys():
    spans = [
        _span(span_id="a", kind="tool_call", attributes={"step.id": "s1", "tool.name": "Read"}),
        _span(span_id="b", kind="tool_call", attributes={"step.id": "s1", "tool.name": "Read"}),
        _span(span_id="c", kind="tool_call", attributes={"step.id": "s1", "tool.name": "WebFetch"}),
        _span(span_id="d", kind="tool_call", attributes={"step.id": "s2", "tool.name": "Read"}),
    ]
    rows = aggregate(spans, group_keys=["step.id", "tool.name"], metrics=["count"])
    assert {(r["step.id"], r["tool.name"], r["count"]) for r in rows} == {
        ("s1", "Read", 2),
        ("s1", "WebFetch", 1),
        ("s2", "Read", 1),
    }


def test_aggregate_missing_key_bucketed_as_none():
    spans = [
        _span(span_id="a", attributes={"item.key": "MNDY"}),
        _span(span_id="b", attributes={}),
    ]
    rows = aggregate(spans, group_keys=["item.key"], metrics=["count"])
    by_ticker = {r["item.key"]: r["count"] for r in rows}
    assert by_ticker["MNDY"] == 1
    assert by_ticker["(none)"] == 1


def test_aggregate_rejects_unknown_metric():
    with pytest.raises(ValueError, match="unsupported metric"):
        aggregate([_span()], group_keys=["kind"], metrics=["wat"])


def test_aggregate_requests_metric_sums_provider_call_requests():
    spans = [
        _span(
            span_id="a",
            kind="provider_call",
            attributes={"provider": "exa", "provider_call.requests": 49},
        ),
        _span(
            span_id="b",
            kind="provider_call",
            attributes={"provider": "perplexity", "provider_call.requests": 0},
        ),
    ]
    rows = aggregate(spans, group_keys=["provider"], metrics=["requests"])
    by_provider = {r["provider"]: r for r in rows}
    assert by_provider["exa"]["requests"] == 49
    assert by_provider["perplexity"]["requests"] == 0


def test_format_rollup_table_emits_columns():
    rows = [
        {"kind": "tool_call", "count": 5},
        {"kind": "subprocess", "count": 2},
    ]
    text = format_rollup_table(rows, group_keys=["kind"], metrics=["count"])
    assert "kind" in text
    assert "count" in text
    assert "tool_call" in text
    assert "5" in text


def test_format_rollup_table_handles_empty():
    assert format_rollup_table([], group_keys=["kind"], metrics=["count"]) == "(no rows)\n"
