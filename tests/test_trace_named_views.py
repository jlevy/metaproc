"""Unit tests for the --health and --cost named views (C4, C5)."""

from __future__ import annotations

from typing import Any

import pytest

from metaproc.trace.named_views import (
    cost_rows,
    format_cost,
    format_health,
    format_source_tool,
    health_rows,
    source_tool_rows,
)
from metaproc.trace.schema import TraceEvent


def _span(**overrides: Any) -> TraceEvent:
    base: dict[str, Any] = {
        "trace_id": "t",
        "span_id": "x",
        "name": "n",
        "kind": "provider_call",
        "source": "web-bundle",
        "ts_start": "2026-05-12T00:00:00Z",
    }
    base.update(overrides)
    return TraceEvent(**base)  # type: ignore[arg-type]


# --- Health view ---


def test_health_rows_groups_non_ok_statuses_only():
    spans = [
        _span(span_id="a", status="ok"),
        _span(span_id="b", status="error"),
        _span(span_id="c", status="silent_failure"),
        _span(span_id="d", status="partial"),
    ]
    rows = health_rows(spans)
    statuses = {r["status"] for r in rows}
    assert statuses == {"error", "silent_failure", "partial"}


def test_health_rows_sorts_silent_failure_first_within_source():
    spans = [
        _span(span_id="a", source="web-bundle", status="partial"),
        _span(span_id="b", source="web-bundle", status="silent_failure"),
        _span(span_id="c", source="web-bundle", status="error"),
    ]
    rows = health_rows(spans)
    web = [r for r in rows if r["source"] == "web-bundle"]
    assert web[0]["status"] == "silent_failure"


def test_health_rows_groups_by_source_then_status():
    spans = [
        _span(span_id="a", source="web-bundle", status="silent_failure"),
        _span(span_id="b", source="web-bundle", status="silent_failure"),
        _span(span_id="c", source="claude-agent", kind="tool_call", status="error"),
    ]
    rows = health_rows(spans)
    web_silent = next(
        r for r in rows if r["source"] == "web-bundle" and r["status"] == "silent_failure"
    )
    assert web_silent["count"] == 2
    claude_error = next(r for r in rows if r["source"] == "claude-agent" and r["status"] == "error")
    assert claude_error["count"] == 1


def test_format_health_renders_empty_message_when_no_failures():
    assert "no failures" in format_health([])


def test_health_rows_clusters_by_error_code_when_present():
    """Two quota-exhausted attempts cluster as one row; auth error is a separate
    row. The 2026-05-13 cascade goal: 191 quota errors should appear as a
    single line, not 191 unattributed entries."""
    spans = [
        _span(
            span_id="a",
            source="claude-agent",
            kind="attempt",
            status="error",
            error={
                "code": "quota_exhausted",
                "message": "You're out of extra usage · resets 3:10am (America/Los_Angeles)",
            },
        ),
        _span(
            span_id="b",
            source="claude-agent",
            kind="attempt",
            status="error",
            error={
                "code": "quota_exhausted",
                "message": "You're out of extra usage · resets 3:10am (America/Los_Angeles)",
            },
        ),
        _span(
            span_id="c",
            source="claude-agent",
            kind="attempt",
            status="error",
            error={"code": "auth_required", "message": "Authentication failed"},
        ),
    ]
    rows = health_rows(spans)
    quota = next(r for r in rows if r["error.code"] == "quota_exhausted")
    auth = next(r for r in rows if r["error.code"] == "auth_required")
    assert quota["count"] == 2
    assert auth["count"] == 1
    assert "out of extra usage" in quota["error.message"]


def test_health_rows_falls_back_to_source_status_when_no_error_code():
    """Legacy non-claude sources without error.code still group by (source, status)."""
    spans = [
        _span(span_id="a", source="web-bundle", status="silent_failure"),
        _span(span_id="b", source="web-bundle", status="silent_failure"),
    ]
    rows = health_rows(spans)
    assert len(rows) == 1
    assert rows[0]["source"] == "web-bundle"
    assert rows[0]["status"] == "silent_failure"
    assert rows[0]["count"] == 2
    assert rows[0]["error.code"] == ""


def test_health_rows_mixed_classified_and_unclassified():
    """When both claude-agent spans (with error.code) and web-bundle spans
    (without) appear, each cluster is its own row."""
    spans = [
        _span(
            span_id="a",
            source="claude-agent",
            kind="attempt",
            status="error",
            error={"code": "quota_exhausted", "message": "out of extra usage"},
        ),
        _span(span_id="b", source="web-bundle", status="silent_failure"),
    ]
    rows = health_rows(spans)
    sources = {r["source"] for r in rows}
    assert sources == {"claude-agent", "web-bundle"}
    claude_row = next(r for r in rows if r["source"] == "claude-agent")
    web_row = next(r for r in rows if r["source"] == "web-bundle")
    assert claude_row["error.code"] == "quota_exhausted"
    assert web_row["error.code"] == ""


def test_format_health_shows_error_code_columns_when_any_row_has_one():
    rows = [
        {
            "source": "claude-agent",
            "status": "error",
            "error.code": "quota_exhausted",
            "error.message": "out of extra usage · resets 3:10am",
            "count": 191,
        }
    ]
    text = format_health(rows)
    assert "quota_exhausted" in text
    assert "out of extra usage" in text
    assert "191" in text


def test_format_health_omits_error_code_columns_when_no_row_has_one():
    """Older traces with no classified errors keep the original 2-column rollup."""
    rows = [{"source": "web-bundle", "status": "silent_failure", "count": 5}]
    text = format_health(rows)
    assert "error.code" not in text
    assert "silent_failure" in text


# --- Cost view ---


def test_cost_rows_sums_cost_usd_attribute():
    spans = [
        _span(span_id="a", source="web-bundle", attributes={"cost.usd": 0.10}),
        _span(span_id="b", source="web-bundle", attributes={"cost.usd": 0.05}),
        _span(
            span_id="c",
            kind="subprocess",
            source="arena-wrapper",
            attributes={"cost.usd": 0.02},
        ),
    ]
    rows = cost_rows(spans)
    web = next(r for r in rows if r["source"] == "web-bundle")
    arena = next(r for r in rows if r["source"] == "arena-wrapper")
    assert web["cost"] == pytest.approx(0.15)
    assert arena["cost"] == pytest.approx(0.02)


def test_cost_rows_promotes_claude_attempt_total_cost_usd():
    """Claude attempt spans carry ``attempt.total_cost_usd`` (not cost.usd);
    the cost view should promote that so the agent total joins cleanly.
    """
    spans = [
        _span(
            span_id="a",
            kind="attempt",
            source="claude-agent",
            attributes={"attempt.total_cost_usd": 0.42},
        ),
        _span(
            span_id="b",
            kind="attempt",
            source="claude-agent",
            attributes={"attempt.total_cost_usd": 0.10},
        ),
    ]
    rows = cost_rows(spans)
    claude = next(r for r in rows if r["source"] == "claude-agent")
    assert claude["cost"] == pytest.approx(0.52)
    assert claude["count"] == 2


def test_cost_rows_omits_spans_without_any_cost_signal():
    spans = [
        _span(span_id="a", attributes={"cost.usd": 0.10}),
        _span(span_id="b"),  # no cost at all
    ]
    rows = cost_rows(spans)
    assert len(rows) == 1
    assert rows[0]["count"] == 1


def test_format_cost_includes_total_line():
    rows = [
        {"source": "web-bundle", "kind": "provider_call", "cost": 0.10, "count": 1},
        {"source": "claude-agent", "kind": "attempt", "cost": 0.42, "count": 1},
    ]
    text = format_cost(rows)
    assert "total cost across trace" in text
    assert "$0.5200" in text or "$0.5200\n" in text


def test_format_cost_empty():
    assert "no cost data" in format_cost([])


# --- Source/tool rollup view ---


def test_source_tool_rows_group_tool_usage_attrs_and_raw_log_ref_coverage():
    spans = [
        _span(
            span_id="a",
            kind="provider_call",
            source="web-bundle",
            status="ok",
            attributes={
                "item.key": "MNDY",
                "item.ticker": "MNDY",
                "step.id": "analysis-research",
                "execution_profile": "codex-gpt55",
                "tool.family": "web",
                "tool.operation": "search",
                "source_origin": "bundle_query",
                "provider": "exa",
                "registry_source_id": "src-1",
                "cutoff_status": "pre_cutoff",
                "raw_log_ref": "/run/bundle.yaml#queries[0].byProvider.exa",
            },
        ),
        _span(
            span_id="b",
            kind="provider_call",
            source="web-bundle",
            status="ok",
            attributes={
                "item.key": "MNDY",
                "item.ticker": "MNDY",
                "step.id": "analysis-research",
                "execution_profile": "codex-gpt55",
                "tool.family": "web",
                "tool.operation": "search",
                "source_origin": "bundle_query",
                "provider": "exa",
                "registry_source_id": "src-1",
                "cutoff_status": "pre_cutoff",
            },
        ),
    ]

    rows = source_tool_rows(spans)
    assert len(rows) == 1
    row = rows[0]
    assert row["item.key"] == "MNDY"
    assert row["source"] == "web-bundle"
    assert row["tool.family"] == "web"
    assert row["tool.operation"] == "search"
    assert row["raw_log_ref.count"] == 1
    assert row["raw_log_ref.coverage_pct"] == 50.0
    text = format_source_tool(rows)
    assert "raw_log_ref.coverage_pct" in text
    assert "bundle_query" in text
