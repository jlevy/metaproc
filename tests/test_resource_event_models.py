"""Unit tests for `metaproc.models.resources` event models (B1, spec §4.4.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from metaproc.logutil.tool_failures import FailureKind
from metaproc.models.resources import (
    BillingEvent,
    HierarchyRef,
    ItemCompleteEvent,
    ItemFailEvent,
    ItemStartEvent,
    Metrics,
    ResourceEvent,
    SampleEvent,
    SourceRef,
    SpanEndEvent,
    SpanStartEvent,
    TaxonomyPaths,
    ToolCallEvent,
    UsageEvent,
    WaitEvent,
)


def _hier(**overrides: str | None) -> HierarchyRef:
    return HierarchyRef(run_id="run-1", **overrides)


def _src(**overrides: object) -> SourceRef:
    base: dict[str, object] = {"kind": "agent_log", "path": ".logs/x.jsonl"}
    base.update(overrides)
    return SourceRef.model_validate(base)


def _ts() -> datetime:
    return datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)


def test_metrics_null_vs_zero() -> None:
    """Spec §4.4.4: missing evidence is None, measured zero is 0."""
    m = Metrics(wall_time_s=0.0, input_tokens=None)
    assert m.wall_time_s == 0.0
    assert m.input_tokens is None


def test_metrics_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Metrics.model_validate({"unknown_metric": 1.0})


def test_hierarchy_requires_run_id() -> None:
    with pytest.raises(ValidationError):
        HierarchyRef.model_validate({})


def test_hierarchy_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        HierarchyRef.model_validate({"run_id": "r1", "totally_made_up": "x"})


def test_taxonomy_extra_paths_default_empty() -> None:
    t = TaxonomyPaths()
    assert t.extra_paths == {}
    assert t.time_kind_path is None


def test_span_start_event_carries_span_id() -> None:
    """span_id is documented as required for span_start in the spec; v1 enforces it
    softly (validation is the joiner's responsibility, not the schema's)."""
    s = SpanStartEvent(ts=_ts(), span_id="sp-1", hierarchy=_hier(), source=_src())
    assert s.span_id == "sp-1"


def test_event_discriminator_default_set_on_each_subtype() -> None:
    """Constructing a typed subclass should default ``event`` to its literal."""
    s = SpanStartEvent(ts=_ts(), span_id="sp", hierarchy=_hier(), source=_src())
    assert s.event == "span_start"
    assert (
        SpanEndEvent(ts=_ts(), span_id="sp", hierarchy=_hier(), source=_src()).event == "span_end"
    )
    assert UsageEvent(ts=_ts(), hierarchy=_hier(), source=_src()).event == "usage"
    assert WaitEvent(ts=_ts(), span_id="sp", hierarchy=_hier(), source=_src()).event == "wait"
    assert (
        ToolCallEvent(ts=_ts(), span_id="sp", hierarchy=_hier(), source=_src()).event == "tool_call"
    )
    assert SampleEvent(ts=_ts(), hierarchy=_hier(), source=_src()).event == "sample"
    assert BillingEvent(ts=_ts(), hierarchy=_hier(), source=_src()).event == "billing"
    assert ItemStartEvent(ts=_ts(), hierarchy=_hier(), source=_src()).event == "item_start"
    assert ItemCompleteEvent(ts=_ts(), hierarchy=_hier(), source=_src()).event == "item_complete"
    assert ItemFailEvent(ts=_ts(), hierarchy=_hier(), source=_src()).event == "item_fail"


def test_item_fail_carries_error_and_failure_class() -> None:
    e = ItemFailEvent(
        ts=_ts(),
        hierarchy=_hier(step_node_id="s1", item_key="AAPL"),
        source=_src(),
        error="boom",
        failure_class="rate_limit",
    )
    assert e.error == "boom"
    assert e.failure_class == "rate_limit"


def test_resource_event_union_discriminates_on_event_field() -> None:
    adapter: TypeAdapter[ResourceEvent] = TypeAdapter(ResourceEvent)
    raw = {
        "ts": "2026-04-24T12:00:00Z",
        "event": "wait",
        "span_id": "sp1",
        "hierarchy": {"run_id": "r1", "step_node_id": "s1"},
        "metrics": {"wait_throttling_s": 4.5},
        "taxonomy": {"time_kind_path": ["waiting", "throttling", "rate_limits", "api"]},
        "source": {"kind": "agent_log", "path": ".logs/x.jsonl", "line_offset": 42},
    }
    parsed = adapter.validate_python(raw)
    assert isinstance(parsed, WaitEvent)
    assert parsed.metrics.wait_throttling_s == 4.5
    assert parsed.taxonomy.time_kind_path == ["waiting", "throttling", "rate_limits", "api"]


def test_resource_event_union_round_trip() -> None:
    """Serialised JSON should round-trip through the discriminated union."""
    adapter: TypeAdapter[ResourceEvent] = TypeAdapter(ResourceEvent)
    original = ItemStartEvent(
        ts=_ts(),
        hierarchy=_hier(step_node_id="s1", item_key="AAPL", worker_id="w0"),
        source=_src(kind="process_events", path=".logs/process-events.jsonl"),
    )
    serialised = adapter.dump_json(original).decode()
    parsed = adapter.validate_json(serialised)
    assert isinstance(parsed, ItemStartEvent)
    assert parsed.hierarchy.item_key == "AAPL"


def test_unknown_event_discriminator_rejected() -> None:
    adapter: TypeAdapter[ResourceEvent] = TypeAdapter(ResourceEvent)
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "ts": "2026-04-24T12:00:00Z",
                "event": "made_up_event",
                "hierarchy": {"run_id": "r"},
                "source": {"kind": "agent_log", "path": "x"},
            }
        )


def test_source_kind_accepts_arena_cli() -> None:
    """Arena CLI emits ResourceEvents with source.kind='arena_cli'."""
    src = SourceRef(kind="arena_cli", path=".logs/tools/arena/resource-events.jsonl")
    assert src.kind == "arena_cli"


def test_tool_call_event_failure_kind_defaults_to_none() -> None:
    e = ToolCallEvent(ts=_ts(), span_id="sp", hierarchy=_hier(), source=_src())
    assert e.failure_kind is None


def test_tool_call_event_carries_failure_kind() -> None:
    e = ToolCallEvent(
        ts=_ts(),
        span_id="sp",
        hierarchy=_hier(tool_name="filtered_web_search"),
        source=_src(kind="arena_cli", path=".logs/tools/arena/resource-events.jsonl"),
        metrics=Metrics(tool_calls=1, tool_failures=1),
        failure_kind=FailureKind.RATE_LIMIT_EXHAUSTED,
    )
    assert e.failure_kind is FailureKind.RATE_LIMIT_EXHAUSTED
    dumped = json.loads(e.model_dump_json())
    assert dumped["failure_kind"] == "rate_limit_exhausted"
    assert dumped["source"]["kind"] == "arena_cli"


def test_tool_call_event_round_trip_with_arena_cli_source_and_failure_kind() -> None:
    """Wire-format round trip for the Arena CLI cutover (Strand C0)."""
    adapter: TypeAdapter[ResourceEvent] = TypeAdapter(ResourceEvent)
    raw = {
        "ts": "2026-04-24T12:00:00Z",
        "event": "tool_call",
        "span_id": "sp",
        "hierarchy": {"run_id": "r1", "tool_name": "filtered_web_search"},
        "source": {
            "kind": "arena_cli",
            "path": ".logs/tools/arena/resource-events.jsonl",
        },
        "failure_kind": "tool_timeout",
    }
    parsed = adapter.validate_python(raw)
    assert isinstance(parsed, ToolCallEvent)
    assert parsed.source.kind == "arena_cli"
    assert parsed.failure_kind is FailureKind.TOOL_TIMEOUT


def test_taxonomy_serialises_lists_not_objects() -> None:
    """Taxonomy paths must persist as JSON arrays (spec §3.1)."""
    e = SpanStartEvent(
        ts=_ts(),
        span_id="sp",
        hierarchy=_hier(),
        source=_src(),
        taxonomy=TaxonomyPaths(
            time_kind_path=["waiting", "throttling"],
            extra_paths={"plugin": ["a", "b"]},
        ),
    )
    dumped = json.loads(e.model_dump_json())
    assert dumped["taxonomy"]["time_kind_path"] == ["waiting", "throttling"]
    assert dumped["taxonomy"]["extra_paths"] == {"plugin": ["a", "b"]}


def test_hierarchy_carries_execution_profile_and_lane_id() -> None:
    """Regression coverage: HierarchyRef must carry execution_profile + lane_id so
    per-lane rollups can group without joining against another table.
    """
    hierarchy = HierarchyRef(
        run_id="run-1",
        step_node_id="analysis-research",
        item_key="CAVA",
        execution_profile="codex-gpt55",
        lane_id="codex-gpt55",
    )
    assert hierarchy.execution_profile == "codex-gpt55"
    assert hierarchy.lane_id == "codex-gpt55"
    # Round-trip safe.
    again = HierarchyRef.model_validate_json(hierarchy.model_dump_json())
    assert again == hierarchy


def test_hierarchy_lane_fields_optional() -> None:
    """Old events without lane context stay valid."""
    hierarchy = HierarchyRef(run_id="run-1")
    assert hierarchy.execution_profile is None
    assert hierarchy.lane_id is None
