"""Unit tests for filter + table + drill + tree views (C2)."""

from __future__ import annotations

from typing import Any

from metaproc.trace.schema import TraceEvent
from metaproc.trace.views import Filter, apply_filter, format_drill, format_table, format_tree


def _span(span_id: str, **overrides: Any) -> TraceEvent:
    base: dict[str, Any] = {
        "trace_id": "run-x",
        "span_id": span_id,
        "name": "test",
        "kind": "tool_call",
        "source": "claude-agent",
        "ts_start": "2026-05-12T00:00:00Z",
    }
    base.update(overrides)
    return TraceEvent(**base)  # pyright: ignore[reportArgumentType]


def test_filter_by_kind():
    spans = [_span("a", kind="tool_call"), _span("b", kind="subprocess")]
    out = apply_filter(spans, Filter(kind="subprocess"))
    assert [s.span_id for s in out] == ["b"]


def test_filter_by_step_id_via_attributes():
    a = _span("a", attributes={"step.id": "research-step"})
    b = _span("b", attributes={"step.id": "deep-market-research"})
    out = apply_filter([a, b], Filter(step="research-step"))
    assert [s.span_id for s in out] == ["a"]


def test_filter_combines_predicates():
    a = _span("a", kind="tool_call", attributes={"item.key": "MNDY", "tool.name": "Read"})
    b = _span("b", kind="tool_call", attributes={"item.key": "MNDY", "tool.name": "WebFetch"})
    c = _span("c", kind="tool_call", attributes={"item.key": "CEVA", "tool.name": "Read"})
    out = apply_filter([a, b, c], Filter(item_key="MNDY", tool="Read"))
    assert [s.span_id for s in out] == ["a"]


def test_filter_by_error_code():
    a = _span(
        "a",
        kind="attempt",
        status="error",
        error={"code": "quota_exhausted", "message": "out of usage"},
    )
    b = _span(
        "b",
        kind="attempt",
        status="error",
        error={"code": "auth_required", "message": "auth failed"},
    )
    c = _span("c", kind="attempt", status="ok")
    out = apply_filter([a, b, c], Filter(error_code="quota_exhausted"))
    assert [s.span_id for s in out] == ["a"]


def test_filter_by_error_code_excludes_spans_with_no_error():
    a = _span("a", kind="attempt", status="ok")
    out = apply_filter([a], Filter(error_code="quota_exhausted"))
    assert out == []


def test_format_table_empty():
    assert format_table([]) == "(no spans)\n"


def test_format_table_includes_header_and_rows():
    spans = [_span("a", name="Read", duration_ms=120.0, attributes={"item.key": "MNDY"})]
    text = format_table(spans)
    assert "kind" in text
    assert "Read" in text
    assert "MNDY" in text
    assert "120ms" in text


def test_format_drill_includes_attributes_and_error():
    span = _span(
        "a",
        attributes={"step.id": "research-step"},
        error={"kind": "tool_error", "message": "boom"},
        status="error",
    )
    text = format_drill(span)
    assert "span_id:" in text
    assert "tool_error" in text
    assert "step.id: research-step" in text


def test_format_tree_renders_parent_child():
    parent = _span(
        "p",
        kind="step",
        attributes={"step.id": "research-step"},
        ts_start="2026-05-12T00:00:00Z",
    )
    child = _span(
        "c",
        kind="item",
        parent_span_id="p",
        attributes={"step.id": "research-step", "item.key": "MNDY"},
        ts_start="2026-05-12T00:00:01Z",
    )
    text = format_tree([parent, child])
    lines = text.strip().splitlines()
    assert any("step:" in line for line in lines)
    indented = [line for line in lines if line.startswith("  ")]
    assert any("item:" in line for line in indented)


def test_format_tree_scoped_to_root_id():
    parent = _span("p", kind="step")
    child_under_p = _span("cp", kind="item", parent_span_id="p")
    other_root = _span("o", kind="step")
    text = format_tree([parent, child_under_p, other_root], root_id="p")
    # Other root should not appear when scoped to "p"
    assert "o" not in text or text.count("step:") == 1
