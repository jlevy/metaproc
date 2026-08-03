"""Resource-budget contracts, evaluation, and plan propagation."""

from __future__ import annotations

import re
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
    MeterKey,
    MeterRollup,
    Metrics,
    Node,
    ResourcesDocument,
)


def _document(
    *,
    metrics: Metrics | None = None,
    meters: list[MeterRollup] | None = None,
) -> ResourcesDocument:
    root = Node(
        node_type="run",
        node_id="run_budget-test",
        label="budget-test",
        total_metrics=metrics or Metrics(),
        total_meters=meters or [],
    )
    return ResourcesDocument(
        run_id="run_budget-test",
        generated_at=datetime(2026, 8, 2, tzinfo=UTC),
        source_events_path=".logs/resource-events.jsonl",
        hierarchy_root=root,
        meter_rollups=meters or [],
    )


def _metric_budget(
    budget_id: str,
    *,
    metric: str,
    threshold: float,
    near_ratio: float = 0.8,
) -> ResourceBudgetSpec:
    return ResourceBudgetSpec.model_validate(
        {
            "budget_id": budget_id,
            "scope": {"kind": "run"},
            "metric": metric,
            "threshold": threshold,
            "near_ratio": near_ratio,
            "posture": "observe",
        }
    )


def test_budget_requires_typed_id_and_exactly_one_target() -> None:
    with pytest.raises(ValidationError, match="bud-"):
        _metric_budget("f09a", metric="api_requests", threshold=10)

    with pytest.raises(ValidationError, match="exactly one"):
        ResourceBudgetSpec.model_validate(
            {
                "budget_id": "bud_invalid-target",
                "scope": {"kind": "run"},
                "metric": "api_requests",
                "meter": {
                    "provider": "serpapi",
                    "product": "google_trends",
                    "meter": "credits",
                    "unit": "credit",
                },
                "threshold": 5,
            }
        )

    with pytest.raises(ValidationError, match="does not match target unit"):
        ResourceBudgetSpec.model_validate(
            {
                "budget_id": "bud_wrong-unit",
                "scope": {"kind": "run"},
                "metric": "api_requests",
                "unit": "tokens",
                "threshold": 5,
            }
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, BudgetStatus.WITHIN),
        (7.99, BudgetStatus.WITHIN),
        (8, BudgetStatus.NEAR),
        (9.99, BudgetStatus.NEAR),
        (10, BudgetStatus.EXCEEDED),
    ],
)
def test_metric_budget_states(value: float, expected: BudgetStatus) -> None:
    document = _document(metrics=Metrics(api_requests=int(value)))
    budget = _metric_budget("bud_api-requests", metric="api_requests", threshold=10)

    evaluation = evaluate_resource_budgets(document, [budget])[0]

    assert evaluation.status is expected
    assert evaluation.coverage is BudgetCoverage.MEASURED
    assert evaluation.actual_quantity == int(value)
    assert evaluation.budget.unit == "count"


def test_budget_preserves_unmeasured_instead_of_fabricating_zero() -> None:
    evaluation = evaluate_resource_budgets(
        _document(),
        [_metric_budget("bud_missing-requests", metric="api_requests", threshold=10)],
    )[0]

    assert evaluation.status is BudgetStatus.UNMEASURED
    assert evaluation.coverage is BudgetCoverage.UNMEASURED
    assert evaluation.actual_quantity is None


def test_exact_meter_budget_preserves_estimate_and_key() -> None:
    key = MeterKey(
        provider="serpapi",
        product="google_trends",
        meter="credits",
        unit="credit",
    )
    rollup = MeterRollup(
        key=key,
        coverage=CoverageState.ESTIMATED,
        estimated_quantity=4,
        source_event_ids=["evt_credit-1"],
    )
    budget = ResourceBudgetSpec.model_validate(
        {
            "budget_id": "bud_trends-credits",
            "scope": {"kind": "run"},
            "meter": key.model_dump(),
            "threshold": 5,
            "near_ratio": 0.75,
            "posture": "warn",
        }
    )

    evaluation = evaluate_resource_budgets(_document(meters=[rollup]), [budget])[0]

    assert evaluation.coverage is BudgetCoverage.ESTIMATED
    assert evaluation.status is BudgetStatus.NEAR
    assert evaluation.actual_quantity is None
    assert evaluation.estimated_quantity == 4
    assert evaluation.budget.meter is not None
    assert evaluation.budget.meter.unit == "credit"
    assert evaluation.budget.unit == "credit"


def test_process_budget_propagates_to_resolved_plan(tmp_path: Path) -> None:
    budget = _metric_budget("bud_run-wall", metric="wall_time_s", threshold=60)
    spec = ProcessSpec(
        name="budgeted",
        resource_budgets=[budget],
        steps=[ProcessStep(id="noop", mode="code", command="true")],
    )

    plan = build_plan(spec, {}, process_path=tmp_path / "test.process.md")

    assert plan.resource_budgets == [budget]


def test_process_rejects_duplicate_budget_ids() -> None:
    budget = _metric_budget("bud_duplicate", metric="wall_time_s", threshold=60)

    with pytest.raises(ValidationError, match="budget IDs must be unique"):
        ProcessSpec(
            name="duplicate-budgets",
            resource_budgets=[budget, budget],
            steps=[ProcessStep(id="noop", mode="code", command="true")],
        )


def test_legacy_step_budgets_project_without_changing_step_fields() -> None:
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

    assert len(budgets) == 2
    assert {budget.metric for budget in budgets} == {"total_tokens", "actual_cost_usd"}
    assert all(re.fullmatch(r"bud-[a-z0-9]{14}", budget.budget_id) for budget in budgets)
    assert all(budget.scope.kind == "step" for budget in budgets)
    assert plan.steps[0].token_budget == 2_000
    assert plan.steps[0].max_budget_usd == 1.5
