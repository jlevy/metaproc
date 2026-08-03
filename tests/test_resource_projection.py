"""Ledger-only resource document projection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from metaproc.engine.resource_rollup import project_resource_document
from metaproc.models.resource_budget import BudgetStatus, ResourceBudgetSpec
from metaproc.models.resources import (
    CoverageState,
    HierarchyRef,
    MeteredQuantity,
    MeterKey,
    Metrics,
    Node,
    SampleEvent,
    SourceRef,
    TaxonomyPaths,
    UsageEvent,
)


def _root() -> Node:
    return Node(node_type="run", node_id="run:run-1", label="run-1")


def _event(*, input_tokens: int = 5) -> UsageEvent:
    return UsageEvent(
        ts=datetime(2026, 8, 3, tzinfo=UTC),
        hierarchy=HierarchyRef(run_id="run-1"),
        metrics=Metrics(input_tokens=input_tokens),
        meters=[
            MeteredQuantity(
                key=MeterKey(
                    provider="openai",
                    product="codex-cli",
                    meter="api_requests",
                    unit="count",
                ),
                coverage=CoverageState.UNMEASURED,
                lineage=["request boundary absent"],
            )
        ],
        taxonomy=TaxonomyPaths(provider_path=["provider", "openai"]),
        source=SourceRef(kind="agent_log", path="session.jsonl"),
    )


def test_duplicate_ledger_events_contribute_to_totals_once() -> None:
    event = _event()

    document = project_resource_document(
        hierarchy_root=_root(),
        run_id="run-1",
        events=[event, event.model_copy(deep=True)],
    )

    assert document.hierarchy_root.total_metrics.input_tokens == 5
    assert document.meter_rollups[0].unmeasured_event_count == 1


def test_conflicting_duplicate_identity_fails_before_projection() -> None:
    event = _event()
    first = project_resource_document(
        hierarchy_root=_root(),
        run_id="run-1",
        events=[event],
    )
    event_id = first.meter_rollups[0].source_event_ids[0]
    conflicting = _event(input_tokens=8).model_copy(update={"event_id": event_id})

    with pytest.raises(ValueError, match="conflicts"):
        project_resource_document(
            hierarchy_root=_root(),
            run_id="run-1",
            events=[event.model_copy(update={"event_id": event_id}), conflicting],
        )


def test_unowned_event_lands_on_run_root_once() -> None:
    document = project_resource_document(
        hierarchy_root=_root(),
        run_id="run-1",
        events=[_event()],
    )

    assert document.hierarchy_root.self_metrics.input_tokens == 5
    assert document.hierarchy_root.total_metrics.input_tokens == 5
    assert document.unattributed.input_tokens is None


def test_unmeasured_meter_is_exposed_as_coverage_gap() -> None:
    document = project_resource_document(
        hierarchy_root=_root(),
        run_id="run-1",
        events=[_event()],
    )

    assert [key.sort_key() for key in document.coverage_gaps] == [
        ("openai", "codex-cli", "api_requests", "count")
    ]


def test_taxonomy_rollup_is_derived_from_ledger_event() -> None:
    event = _event()
    event.metrics.list_cost_usd = 1.25

    document = project_resource_document(
        hierarchy_root=_root(),
        run_id="run-1",
        events=[event],
    )

    provider_rollup = document.taxonomy_rollups["provider_path"][-1]
    assert provider_rollup.path == ["provider", "openai"]
    assert provider_rollup.metrics.list_cost_usd == pytest.approx(1.25)


def test_budget_evaluation_is_derived_from_projected_ledger() -> None:
    budget = ResourceBudgetSpec.model_validate(
        {
            "budget_id": "bud-input-limit",
            "metric": "input_tokens",
            "threshold": 5,
        }
    )

    document = project_resource_document(
        hierarchy_root=_root(),
        run_id="run-1",
        events=[_event()],
        budgets=[budget],
    )

    assert document.budget_evaluations[0].status is BudgetStatus.EXCEEDED
    assert document.budget_evaluations[0].actual_quantity == 5


def test_sample_averages_are_weighted_by_raw_evidence_across_children() -> None:
    root = Node(
        node_type="run",
        node_id="run:run-1",
        label="run-1",
        children=[
            Node(node_type="step", node_id="first", label="first", parent_id="run:run-1"),
            Node(node_type="step", node_id="second", label="second", parent_id="run:run-1"),
        ],
    )
    events = [
        SampleEvent(
            ts=datetime(2026, 8, 3, minute=index, tzinfo=UTC),
            hierarchy=HierarchyRef(run_id="run-1", step_node_id=step),
            metrics=Metrics(
                cpu_pct_avg=cpu,
                cpu_pct_max=cpu,
                rss_bytes_avg=rss,
                rss_bytes_max=int(rss),
            ),
            source=SourceRef(kind="sample", path=f"{step}-{index}.jsonl"),
        )
        for index, (step, cpu, rss) in enumerate(
            [
                ("first", 10.0, 100.0),
                ("second", 20.0, 200.0),
                ("second", 30.0, 300.0),
                ("second", 40.0, 400.0),
            ]
        )
    ]

    document = project_resource_document(
        hierarchy_root=root,
        run_id="run-1",
        events=events,
    )

    assert document.hierarchy_root.total_metrics.cpu_pct_avg == pytest.approx(25.0)
    assert document.hierarchy_root.total_metrics.cpu_pct_max == pytest.approx(40.0)
    assert document.hierarchy_root.total_metrics.rss_bytes_avg == pytest.approx(250.0)
    assert document.hierarchy_root.total_metrics.rss_bytes_max == 400
