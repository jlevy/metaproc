"""Deterministic resource-event reconciliation tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from metaproc.engine.resource_reconciliation import (
    aggregate_meter_rollups,
    ensure_resource_event_id,
    reconcile_resource_events,
)
from metaproc.models.resources import (
    CoverageState,
    HierarchyRef,
    MeteredQuantity,
    MeterKey,
    Metrics,
    SourceRef,
    UsageEvent,
)


def _event(*, mtime_ns: int = 1, input_tokens: int = 3) -> UsageEvent:
    return UsageEvent(
        ts=datetime(2026, 8, 3, tzinfo=UTC),
        hierarchy=HierarchyRef(run_id="run-1", step_node_id="step-1"),
        metrics=Metrics(input_tokens=input_tokens),
        source=SourceRef(
            kind="agent_log",
            path="step-1/.logs/session.jsonl",
            line_offset=4,
            size_bytes=100,
            mtime_ns=mtime_ns,
        ),
    )


def test_derived_event_id_ignores_volatile_file_metadata() -> None:
    first = ensure_resource_event_id(_event(mtime_ns=1))
    changed_metadata = _event(mtime_ns=999)
    changed_metadata.source.size_bytes = 500
    second = ensure_resource_event_id(changed_metadata)

    assert first.event_id == second.event_id
    assert first.event_id is not None
    assert first.event_id.startswith("evt-")


def test_reconciliation_deduplicates_identical_event_ids() -> None:
    event = ensure_resource_event_id(_event())
    assert reconcile_resource_events([event, event.model_copy(deep=True)]) == [event]


def test_reconciliation_rejects_conflicting_event_ids() -> None:
    first = ensure_resource_event_id(_event(input_tokens=3))
    conflicting = _event(input_tokens=4).model_copy(update={"event_id": first.event_id})

    with pytest.raises(ValueError, match="conflicts"):
        reconcile_resource_events([first, conflicting])


def test_producer_span_identity_detects_changed_evidence() -> None:
    first = _event(input_tokens=3).model_copy(update={"span_id": "provider:attempt-1"})
    changed = _event(input_tokens=4).model_copy(update={"span_id": "provider:attempt-1"})

    assert ensure_resource_event_id(first).event_id == ensure_resource_event_id(changed).event_id
    with pytest.raises(ValueError, match="conflicts"):
        reconcile_resource_events([first, changed])


def test_meter_rollups_keep_actual_estimated_and_unknown_distinct() -> None:
    key = MeterKey(
        provider="anthropic",
        product="claude-code",
        meter="server_tool_requests",
        unit="count",
    )
    events = [
        ensure_resource_event_id(
            _event().model_copy(
                update={
                    "meters": [
                        MeteredQuantity(
                            key=key,
                            coverage=CoverageState.MEASURED,
                            actual_quantity=0,
                            lineage=["usage.server_tool_use"],
                        )
                    ]
                }
            )
        ),
        ensure_resource_event_id(
            _event(input_tokens=4).model_copy(
                update={
                    "meters": [
                        MeteredQuantity(
                            key=key,
                            coverage=CoverageState.ESTIMATED,
                            estimated_quantity=2,
                            lineage=["terminal.aggregate"],
                        )
                    ]
                }
            )
        ),
        ensure_resource_event_id(
            _event(input_tokens=5).model_copy(
                update={
                    "meters": [
                        MeteredQuantity(
                            key=key,
                            coverage=CoverageState.UNMEASURED,
                            lineage=["provider request boundary absent"],
                        )
                    ]
                }
            )
        ),
    ]

    rollup = aggregate_meter_rollups(events)[0]
    assert rollup.coverage is CoverageState.UNMEASURED
    assert rollup.actual_quantity == 0
    assert rollup.estimated_quantity == 2
    assert rollup.unmeasured_event_count == 1
    assert len(rollup.source_event_ids) == 3


def test_complete_mixed_meter_rollup_is_estimated() -> None:
    key = MeterKey(
        provider="example",
        product="api",
        meter="credits",
        unit="credit",
    )
    events = [
        ensure_resource_event_id(
            _event(input_tokens=index).model_copy(
                update={
                    "meters": [
                        MeteredQuantity(
                            key=key,
                            coverage=coverage,
                            actual_quantity=quantity
                            if coverage is CoverageState.MEASURED
                            else None,
                            estimated_quantity=(
                                quantity if coverage is CoverageState.ESTIMATED else None
                            ),
                            lineage=["provider fact" if index == 1 else "documented estimate"],
                        )
                    ]
                }
            )
        )
        for index, coverage, quantity in [
            (1, CoverageState.MEASURED, 3),
            (2, CoverageState.ESTIMATED, 4),
        ]
    ]

    rollup = aggregate_meter_rollups(events)[0]

    assert rollup.coverage is CoverageState.ESTIMATED
    assert rollup.actual_quantity == 3
    assert rollup.estimated_quantity == 4
    assert rollup.unmeasured_event_count == 0
