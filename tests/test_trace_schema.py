"""Unit tests for ``TraceEvent/0.1`` schema, span_id, and linker."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from metaproc.trace import (
    SEVERITY_ORDER,
    SPAN_KINDS,
    SPAN_STATUSES,
    TraceEvent,
    compute_span_id,
    link_and_propagate,
)
from metaproc.trace.schema import TOOL_USAGE_ATTRS, bounded_attr_text, tool_usage_attrs


def _minimal_event(**overrides: Any) -> TraceEvent:
    defaults: dict[str, Any] = {
        "trace_id": "run-x",
        "span_id": "abc1234567890def",
        "name": "test",
        "kind": "step",
        "source": "metaproc-engine",
        "ts_start": "2026-05-12T00:00:00Z",
    }
    defaults.update(overrides)
    return TraceEvent(**defaults)  # pyright: ignore[reportArgumentType]


def test_trace_event_minimal_fields():
    ev = _minimal_event()
    assert ev.trace_id == "run-x"
    assert ev.kind == "step"
    assert ev.status == "unknown"
    assert ev.status_derived is False
    assert ev.attributes == {}
    assert ev.events == []
    assert ev.parent_span_id is None
    assert ev.ts_end is None
    assert ev.duration_ms is None


def test_trace_event_rejects_extra_fields():
    with pytest.raises(ValidationError):
        TraceEvent(
            trace_id="x",
            span_id="abc",
            name="t",
            kind="step",
            source="s",
            ts_start="2026-01-01T00:00:00Z",
            unknown_field="boom",  # pyright: ignore[reportCallIssue]
        )


def test_trace_event_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        _minimal_event(kind="not_a_kind")


def test_trace_event_rejects_unknown_status():
    with pytest.raises(ValidationError):
        _minimal_event(status="weird")


def test_trace_event_severity_method():
    assert _minimal_event(status="ok").severity() == SEVERITY_ORDER["ok"]
    assert _minimal_event(status="silent_failure").severity() == SEVERITY_ORDER["silent_failure"]
    assert _minimal_event(status="error").severity() == SEVERITY_ORDER["error"]


def test_span_kinds_and_statuses_are_frozen():
    assert isinstance(SPAN_KINDS, frozenset)
    assert isinstance(SPAN_STATUSES, frozenset)
    assert "tool_call" in SPAN_KINDS
    assert "silent_failure" in SPAN_STATUSES


def test_tool_usage_attr_contract_is_code_owned_and_backward_compatible():
    assert "tool.family" in TOOL_USAGE_ATTRS
    assert "tool.operation" in TOOL_USAGE_ATTRS
    assert "source_origin" in TOOL_USAGE_ATTRS
    assert "raw_log_ref" in TOOL_USAGE_ATTRS
    attrs = tool_usage_attrs(tool__family="web", provider=None, source_origin="")
    assert attrs == {"tool.family": "web"}
    assert bounded_attr_text("  abc  ", max_chars=2) == "ab"
    # Attributes stay free-form; adding contract constants did not add required fields.
    assert _minimal_event(attributes=attrs).attributes["tool.family"] == "web"


def test_compute_span_id_is_deterministic():
    a = compute_span_id("claude-agent", "/path/to/x.jsonl", "tool_use_123")
    b = compute_span_id("claude-agent", "/path/to/x.jsonl", "tool_use_123")
    assert a == b
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)


def test_compute_span_id_distinguishes_sources():
    a = compute_span_id("claude-agent", "k1")
    b = compute_span_id("arena-wrapper", "k1")
    assert a != b


def test_compute_span_id_distinguishes_part_boundaries():
    """Two parts ``("ab", "c")`` must not collide with one part ``"abc"``."""
    a = compute_span_id("s", "ab", "c")
    b = compute_span_id("s", "abc")
    assert a != b


def test_compute_span_id_rejects_empty_source():
    with pytest.raises(ValueError, match="source must be non-empty"):
        compute_span_id("", "k")


def test_linker_propagates_error_upward():
    parent = _minimal_event(
        span_id="parent00000000000",
        kind="step",
        status="ok",
        attributes={"step.id": "research-step"},
    )
    child = _minimal_event(
        span_id="child0000000000",
        kind="item",
        status="error",
        parent_span_id="parent00000000000",
        attributes={"step.id": "research-step", "item.key": "MNDY"},
    )
    linked = link_and_propagate([parent, child])
    by_id = {s.span_id: s for s in linked}
    assert by_id["parent00000000000"].status == "error"
    # Original status preserved
    assert by_id["parent00000000000"].attributes["source.status"] == "ok"
    assert by_id["child0000000000"].status == "error"


def test_linker_silent_failure_outranks_partial():
    parent = _minimal_event(span_id="p1", status="partial", kind="step")
    child = _minimal_event(
        span_id="c1",
        status="silent_failure",
        kind="provider_call",
        parent_span_id="p1",
    )
    linked = link_and_propagate([parent, child])
    by_id = {s.span_id: s for s in linked}
    assert by_id["p1"].status == "silent_failure"


def test_linker_error_outranks_silent_failure():
    parent = _minimal_event(span_id="p1", status="silent_failure", kind="step")
    child = _minimal_event(
        span_id="c1",
        status="error",
        kind="provider_call",
        parent_span_id="p1",
    )
    linked = link_and_propagate([parent, child])
    by_id = {s.span_id: s for s in linked}
    assert by_id["p1"].status == "error"


def test_linker_links_attempt_to_item_cross_source():
    """An attempt span without ``parent_span_id`` set should be linked to the
    matching item span via ``(step.id, item.key)``.
    """
    item = _minimal_event(
        span_id="item0000",
        kind="item",
        source="metaproc-engine",
        attributes={"step.id": "research-step", "item.key": "MNDY"},
    )
    attempt = _minimal_event(
        span_id="att00000",
        kind="attempt",
        source="claude-agent",
        attributes={"step.id": "research-step", "item.key": "MNDY"},
    )
    linked = link_and_propagate([item, attempt])
    by_id = {s.span_id: s for s in linked}
    assert by_id["att00000"].parent_span_id == "item0000"


def test_linker_keeps_same_named_items_isolated_by_scope():
    root_item = _minimal_event(
        span_id="rootitem",
        kind="item",
        source="metaproc-engine",
        attributes={"scope.path": ".", "step.id": "research", "item.key": "UI"},
    )
    child_item = _minimal_event(
        span_id="childitm",
        kind="item",
        source="metaproc-engine",
        attributes={
            "scope.path": "query-plan/UI",
            "step.id": "research",
            "item.key": "UI",
        },
    )
    child_attempt = _minimal_event(
        span_id="childatt",
        kind="attempt",
        source="gemini-agent",
        attributes={
            "scope.path": "query-plan/UI",
            "step.id": "research",
            "item.key": "UI",
        },
    )

    linked = link_and_propagate([child_item, root_item, child_attempt])
    by_id = {span.span_id: span for span in linked}
    assert by_id["childatt"].parent_span_id == "childitm"


def test_linker_falls_back_from_scalar_attempt_to_scoped_step():
    step = _minimal_event(
        span_id="childstep",
        kind="step",
        source="metaproc-engine",
        attributes={"scope.path": "query-plan/UI", "step.id": "generate"},
    )
    attempt = _minimal_event(
        span_id="childatt",
        kind="attempt",
        source="gemini-agent",
        attributes={
            "scope.path": "query-plan/UI",
            "step.id": "generate",
            "item.key": "attempt.jsonl",
        },
    )

    linked = link_and_propagate([step, attempt])
    by_id = {span.span_id: span for span in linked}
    assert by_id["childatt"].parent_span_id == "childstep"


def test_linker_links_subprocess_to_step():
    step = _minimal_event(
        span_id="step000",
        kind="step",
        source="metaproc-engine",
        attributes={"step.id": "web-research-bundle"},
    )
    arena = _minimal_event(
        span_id="arena00",
        kind="subprocess",
        source="arena-wrapper",
        attributes={"step.id": "web-research-bundle"},
    )
    linked = link_and_propagate([step, arena])
    by_id = {s.span_id: s for s in linked}
    assert by_id["arena00"].parent_span_id == "step000"


def test_linker_links_provider_call_to_arena_by_bundle_path():
    arena = _minimal_event(
        span_id="arena00",
        kind="subprocess",
        source="arena-wrapper",
        attributes={"subprocess.out_path": "/run/MNDY/web-research-bundle.json"},
    )
    provider = _minimal_event(
        span_id="prov000",
        kind="provider_call",
        source="web-bundle",
        attributes={"subprocess.out_path": "/run/MNDY/web-research-bundle.json"},
    )
    linked = link_and_propagate([arena, provider])
    by_id = {s.span_id: s for s in linked}
    assert by_id["prov000"].parent_span_id == "arena00"


def test_linker_idempotent():
    """Running the linker twice produces the same output."""
    parent = _minimal_event(span_id="p1", status="ok", kind="step")
    child = _minimal_event(
        span_id="c1",
        status="error",
        kind="item",
        parent_span_id="p1",
    )
    once = link_and_propagate([parent, child])
    twice = link_and_propagate(once)
    once_dict = [s.model_dump() for s in once]
    twice_dict = [s.model_dump() for s in twice]
    assert once_dict == twice_dict
