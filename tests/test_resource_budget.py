"""Resource-budget contracts, evaluation, and plan propagation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from metaproc.engine.build_plan import build_plan
from metaproc.models.authored import ProcessSpec, ProcessStep
from metaproc.models.plan import Plan, ResolvedStep
from metaproc.models.resource_budget import (
    BudgetCoverage,
    BudgetStatus,
    ResourceBudgetSpec,
    collect_resource_budgets,
    evaluate_resource_budgets,
)
from metaproc.models.resources import (
    CoverageState,
    HierarchyRef,
    MeteredQuantity,
    MeterKey,
    MeterRollup,
    Metrics,
    Node,
    ProviderRef,
    ResourcesDocument,
    SourceRef,
    UsageEvent,
)


def _document(
    *,
    metrics: Metrics | None = None,
    meters: list[MeterRollup] | None = None,
) -> ResourcesDocument:
    root = Node(
        node_type="run",
        node_id="run-budget-test",
        label="budget-test",
        total_metrics=metrics or Metrics(),
        total_meters=meters or [],
    )
    return ResourcesDocument(
        run_id="run-budget-test",
        generated_at=datetime(2026, 8, 3, tzinfo=UTC),
        source_events_path=".logs/resource-events.jsonl",
        hierarchy_root=root,
        meter_rollups=meters or [],
    )


def _metric_budget(
    budget_id: str,
    *,
    metric: str,
    threshold: float,
) -> ResourceBudgetSpec:
    return ResourceBudgetSpec.model_validate(
        {
            "budget_id": budget_id,
            "scope": {"kind": "run"},
            "metric": metric,
            "threshold": threshold,
        }
    )


def test_budget_requires_typed_id_and_exactly_one_target() -> None:
    with pytest.raises(ValidationError):
        _metric_budget("plain", metric="api_requests", threshold=10)

    with pytest.raises(ValidationError, match="exactly one"):
        ResourceBudgetSpec.model_validate(
            {
                "budget_id": "bud-invalid-target",
                "metric": "api_requests",
                "meter": {
                    "provider": "serpapi",
                    "product": "google-trends",
                    "meter": "credits",
                    "unit": "credit",
                },
                "threshold": 5,
            }
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, BudgetStatus.WITHIN),
        (7, BudgetStatus.WITHIN),
        (8, BudgetStatus.NEAR),
        (10, BudgetStatus.EXCEEDED),
    ],
)
def test_metric_budget_states(value: int, expected: BudgetStatus) -> None:
    evaluation = evaluate_resource_budgets(
        _document(metrics=Metrics(api_requests=value)),
        [_metric_budget("bud-api-requests", metric="api_requests", threshold=10)],
    )[0]
    assert evaluation.status is expected
    assert evaluation.coverage is BudgetCoverage.MEASURED
    assert evaluation.actual_quantity == value


def test_budget_preserves_unmeasured_instead_of_fabricating_zero() -> None:
    evaluation = evaluate_resource_budgets(
        _document(),
        [_metric_budget("bud-missing", metric="api_requests", threshold=10)],
    )[0]
    assert evaluation.status is BudgetStatus.UNMEASURED
    assert evaluation.coverage is BudgetCoverage.UNMEASURED
    assert evaluation.actual_quantity is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, BudgetStatus.WITHIN), (1, BudgetStatus.EXCEEDED)],
)
def test_zero_threshold_allows_only_measured_zero(
    value: int,
    expected: BudgetStatus,
) -> None:
    evaluation = evaluate_resource_budgets(
        _document(metrics=Metrics(api_requests=value)),
        [_metric_budget("bud-zero-requests", metric="api_requests", threshold=0)],
    )[0]

    assert evaluation.status is expected


def test_incomplete_meter_cannot_report_exceeded() -> None:
    key = MeterKey(
        provider="serpapi",
        product="google-trends",
        meter="credits",
        unit="credit",
    )
    rollup = MeterRollup(
        key=key,
        coverage=CoverageState.UNMEASURED,
        actual_quantity=100,
        unmeasured_event_count=1,
        source_event_ids=["evt-credit-1"],
        lineage=["one request had no credit response"],
    )
    budget = ResourceBudgetSpec.model_validate(
        {
            "budget_id": "bud-trends-credits",
            "meter": key.model_dump(),
            "threshold": 5,
        }
    )
    evaluation = evaluate_resource_budgets(_document(meters=[rollup]), [budget])[0]
    assert evaluation.coverage is BudgetCoverage.UNMEASURED
    assert evaluation.status is BudgetStatus.UNMEASURED


def test_process_budget_propagates_to_resolved_plan(tmp_path: Path) -> None:
    budget = _metric_budget("bud-run-wall", metric="wall_time_s", threshold=60)
    spec = ProcessSpec(
        name="budgeted",
        resource_budgets=[budget],
        steps=[ProcessStep(id="noop", mode="code", command="true")],
    )
    plan = build_plan(spec, {}, process_path=tmp_path / "test.process.md")
    assert plan.resource_budgets == [budget]


def test_process_rejects_duplicate_budget_ids() -> None:
    budget = _metric_budget("bud-duplicate", metric="wall_time_s", threshold=60)
    with pytest.raises(ValidationError, match="budget IDs must be unique"):
        ProcessSpec(
            name="duplicate-budgets",
            resource_budgets=[budget, budget],
            steps=[ProcessStep(id="noop", mode="code", command="true")],
        )


def test_legacy_step_budgets_project_to_tokens_and_list_cost() -> None:
    plan = Plan(
        process="test.process.md",
        steps=[
            ResolvedStep(
                step_id="research",
                mode="agent",
                token_budget=2_000,
                max_budget_usd=1.5,
            )
        ],
    )
    budgets = collect_resource_budgets(plan)
    assert {budget.metric.value for budget in budgets if budget.metric} == {
        "total_tokens",
        "list_cost_usd",
    }
    assert all(budget.budget_id.startswith("bud-") for budget in budgets)


def test_model_scoped_meter_uses_only_matching_provider_events() -> None:
    key = MeterKey(
        provider="anthropic",
        product="claude-code",
        meter="turns",
        unit="count",
    )
    events = [
        UsageEvent(
            ts=datetime(2026, 8, 3, tzinfo=UTC),
            hierarchy=HierarchyRef(run_id="run-budget-test"),
            provider=ProviderRef(provider="anthropic", product="claude-code", model=model),
            meters=[
                MeteredQuantity(
                    key=key,
                    coverage=CoverageState.MEASURED,
                    actual_quantity=quantity,
                )
            ],
            source=SourceRef(kind="agent_log", path=f"{model}.jsonl"),
        )
        for model, quantity in [("opus", 2), ("sonnet", 9)]
    ]
    document = _document(
        meters=[
            MeterRollup(
                key=key,
                coverage=CoverageState.MEASURED,
                actual_quantity=11,
                source_event_ids=["evt-all"],
            )
        ]
    )
    budget = ResourceBudgetSpec.model_validate(
        {
            "budget_id": "bud-opus-turns",
            "scope": {"kind": "model", "key": "opus"},
            "meter": key.model_dump(),
            "threshold": 2.5,
        }
    )

    evaluation = evaluate_resource_budgets(document, [budget], events=events)[0]

    assert evaluation.actual_quantity == 2
    assert evaluation.status is BudgetStatus.NEAR


def test_model_metric_ignores_meter_only_events() -> None:
    provider = ProviderRef(provider="anthropic", product="claude-code", model="opus")
    events = [
        UsageEvent(
            ts=datetime(2026, 8, 3, tzinfo=UTC),
            hierarchy=HierarchyRef(run_id="run-budget-test"),
            provider=provider,
            metrics=Metrics(input_tokens=7),
            source=SourceRef(kind="agent_log", path="usage.jsonl"),
        ),
        UsageEvent(
            ts=datetime(2026, 8, 3, tzinfo=UTC),
            hierarchy=HierarchyRef(run_id="run-budget-test"),
            provider=provider,
            meters=[
                MeteredQuantity(
                    key=MeterKey(
                        provider="anthropic",
                        product="claude-code",
                        meter="turns",
                        unit="count",
                    ),
                    coverage=CoverageState.MEASURED,
                    actual_quantity=1,
                )
            ],
            source=SourceRef(kind="agent_log", path="meters.jsonl"),
        ),
    ]
    budget = ResourceBudgetSpec.model_validate(
        {
            "budget_id": "bud-opus-input",
            "scope": {"kind": "model", "key": "opus"},
            "metric": "input_tokens",
            "threshold": 10,
        }
    )

    evaluation = evaluate_resource_budgets(_document(), [budget], events=events)[0]

    assert evaluation.coverage is BudgetCoverage.MEASURED
    assert evaluation.actual_quantity == 7


def test_tool_scope_aggregates_matching_leaves_across_logs() -> None:
    root = Node(
        node_type="run",
        node_id="run-budget-test",
        label="budget-test",
        children=[
            Node(
                node_type="tool",
                node_id="tool:first:Bash",
                label="Bash",
                total_metrics=Metrics(tool_calls=2),
            ),
            Node(
                node_type="tool",
                node_id="tool:second:Bash",
                label="Bash",
                total_metrics=Metrics(tool_calls=3),
            ),
        ],
    )
    document = ResourcesDocument(
        run_id="run-budget-test",
        generated_at=datetime(2026, 8, 3, tzinfo=UTC),
        source_events_path=".logs/resource-events.jsonl",
        hierarchy_root=root,
    )
    budget = ResourceBudgetSpec.model_validate(
        {
            "budget_id": "bud-bash-calls",
            "scope": {"kind": "tool", "key": "Bash"},
            "metric": "tool_calls",
            "threshold": 4,
        }
    )

    evaluation = evaluate_resource_budgets(document, [budget])[0]

    assert evaluation.coverage is BudgetCoverage.MEASURED
    assert evaluation.actual_quantity == 5
    assert evaluation.status is BudgetStatus.EXCEEDED
