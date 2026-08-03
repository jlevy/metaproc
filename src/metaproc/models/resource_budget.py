"""Resource-budget contracts and deterministic reporting-only evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from metaproc.ids import derive_typed_id_from_key, require_typed_id

if TYPE_CHECKING:
    from metaproc.models.plan import Plan
    from metaproc.models.resources import (
        MeterRollup,
        Metrics,
        Node,
        ResourceEvent,
        ResourcesDocument,
    )


class BudgetStatus(StrEnum):
    """Threshold evaluation state."""

    WITHIN = "within"
    NEAR = "near"
    EXCEEDED = "exceeded"
    UNMEASURED = "unmeasured"


class BudgetCoverage(StrEnum):
    """Evidence quality used by one evaluation."""

    MEASURED = "measured"
    ESTIMATED = "estimated"
    UNMEASURED = "unmeasured"


class BudgetPosture(StrEnum):
    """Authored operator response metadata.

    Evaluation records posture but does not add a dispatch-refusal path.
    """

    OBSERVE = "observe"
    WARN = "warn"
    REFUSE_NEW_WORK = "refuse-new-work"


class BudgetScopeKind(StrEnum):
    """Hierarchy or provider slice selected by a budget."""

    RUN = "run"
    PROCESS = "process"
    STEP = "step"
    PROVIDER = "provider"
    PRODUCT = "product"
    MODEL = "model"
    TOOL = "tool"


class BudgetMetric(StrEnum):
    """Canonical scalar resource metric."""

    WALL_TIME_S = "wall_time_s"
    ACTIVE_CPU_S = "active_cpu_s"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    CACHE_READ_TOKENS = "cache_read_tokens"
    CACHE_WRITE_TOKENS = "cache_write_tokens"
    TOTAL_TOKENS = "total_tokens"
    ACTUAL_COST_USD = "actual_cost_usd"
    LIST_COST_USD = "list_cost_usd"
    TOOL_CALLS = "tool_calls"
    TOOL_FAILURES = "tool_failures"
    API_REQUESTS = "api_requests"
    API_FAILURES = "api_failures"
    RETRIES = "retries"
    CACHE_HITS = "cache_hits"
    CACHE_MISSES = "cache_misses"
    WAIT_THROTTLING_S = "wait_throttling_s"
    WAIT_RATE_LIMIT_S = "wait_rate_limit_s"
    WAIT_BUDGET_S = "wait_budget_s"
    WAIT_NETWORK_S = "wait_network_s"
    TOOL_EXEC_S = "tool_exec_s"
    LOCAL_COMPUTE_S = "local_compute_s"


_METRIC_UNITS: dict[BudgetMetric, str] = {
    BudgetMetric.WALL_TIME_S: "seconds",
    BudgetMetric.ACTIVE_CPU_S: "seconds",
    BudgetMetric.INPUT_TOKENS: "tokens",
    BudgetMetric.OUTPUT_TOKENS: "tokens",
    BudgetMetric.CACHE_READ_TOKENS: "tokens",
    BudgetMetric.CACHE_WRITE_TOKENS: "tokens",
    BudgetMetric.TOTAL_TOKENS: "tokens",
    BudgetMetric.ACTUAL_COST_USD: "usd",
    BudgetMetric.LIST_COST_USD: "usd",
    BudgetMetric.TOOL_CALLS: "count",
    BudgetMetric.TOOL_FAILURES: "count",
    BudgetMetric.API_REQUESTS: "count",
    BudgetMetric.API_FAILURES: "count",
    BudgetMetric.RETRIES: "count",
    BudgetMetric.CACHE_HITS: "count",
    BudgetMetric.CACHE_MISSES: "count",
    BudgetMetric.WAIT_THROTTLING_S: "seconds",
    BudgetMetric.WAIT_RATE_LIMIT_S: "seconds",
    BudgetMetric.WAIT_BUDGET_S: "seconds",
    BudgetMetric.WAIT_NETWORK_S: "seconds",
    BudgetMetric.TOOL_EXEC_S: "seconds",
    BudgetMetric.LOCAL_COMPUTE_S: "seconds",
}


class BudgetScope(BaseModel):
    """One run, hierarchy node, or provider/product/model slice."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: BudgetScopeKind = BudgetScopeKind.RUN
    key: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_key(self) -> Self:
        if self.kind is BudgetScopeKind.RUN and self.key is not None:
            raise ValueError("run budget scope must not provide a key")
        if self.kind is not BudgetScopeKind.RUN and self.key is None:
            raise ValueError(f"{self.kind.value} budget scope requires a key")
        return self


class BudgetMeterKey(BaseModel):
    """Exact provider-meter selector used by an authored budget."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=128)
    product: str = Field(min_length=1, max_length=128)
    meter: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=64)

    @field_validator("provider", "product", "meter", "unit")
    @classmethod
    def _validate_component(cls, value: str) -> str:
        if value != value.strip() or any(char.isspace() for char in value):
            raise ValueError("budget meter-key components must be tokens without whitespace")
        return value

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.provider, self.product, self.meter, self.unit)


class ResourceBudgetSpec(BaseModel):
    """One immutable threshold over a scalar metric or exact meter."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    budget_id: str
    scope: BudgetScope = Field(default_factory=BudgetScope)
    metric: BudgetMetric | None = None
    meter: BudgetMeterKey | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    threshold: float = Field(ge=0)
    near_ratio: float = Field(default=0.8, gt=0, le=1)
    posture: BudgetPosture = BudgetPosture.OBSERVE
    source: Literal["authored", "legacy"] = "authored"
    description: str | None = Field(default=None, max_length=500)

    @field_validator("budget_id")
    @classmethod
    def _validate_budget_id(cls, value: str) -> str:
        return require_typed_id(value, "bud")

    @field_validator("unit")
    @classmethod
    def _validate_unit(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or any(char.isspace() for char in value)):
            raise ValueError("budget unit must be a token without whitespace")
        return value

    @model_validator(mode="after")
    def _validate_target(self) -> Self:
        if (self.metric is None) == (self.meter is None):
            raise ValueError("resource budget must define exactly one of metric or meter")
        expected_unit = (
            _METRIC_UNITS[self.metric]
            if self.metric is not None
            else cast(BudgetMeterKey, self.meter).unit
        )
        if self.unit is None:
            self.unit = expected_unit
        elif self.unit != expected_unit:
            raise ValueError(
                f"budget unit {self.unit!r} does not match target unit {expected_unit!r}"
            )
        if self.scope.kind is BudgetScopeKind.PROVIDER and self.meter is not None:
            if self.scope.key != self.meter.provider:
                raise ValueError("provider-scoped meter budget must match the meter provider")
        if self.scope.kind is BudgetScopeKind.PRODUCT and self.meter is not None:
            if self.scope.key != self.meter.product:
                raise ValueError("product-scoped meter budget must match the meter product")
        return self


class BudgetEvaluation(BaseModel):
    """Observed quantity and threshold state for one budget."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    budget: ResourceBudgetSpec
    status: BudgetStatus
    coverage: BudgetCoverage
    actual_quantity: float | None = Field(default=None, ge=0)
    estimated_quantity: float | None = Field(default=None, ge=0)
    message: str

    @model_validator(mode="after")
    def _validate_coverage(self) -> Self:
        if self.coverage is BudgetCoverage.MEASURED:
            valid = self.actual_quantity is not None and self.estimated_quantity is None
        elif self.coverage is BudgetCoverage.ESTIMATED:
            valid = self.actual_quantity is None and self.estimated_quantity is not None
        else:
            valid = self.actual_quantity is None and self.estimated_quantity is None
        if not valid:
            raise ValueError("budget coverage must populate only its matching quantity field")
        if (
            self.status is BudgetStatus.UNMEASURED
            and self.coverage is not BudgetCoverage.UNMEASURED
        ):
            raise ValueError("only unmeasured coverage may produce unmeasured budget status")
        if (
            self.coverage is BudgetCoverage.UNMEASURED
            and self.status is not BudgetStatus.UNMEASURED
        ):
            raise ValueError("unmeasured budget coverage requires unmeasured status")
        return self


class FinalizationState(StrEnum):
    """Terminal state retained on a resource report."""

    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ResourceFinalization(BaseModel):
    """How and when a terminal resource projection was produced."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    state: FinalizationState
    trigger: Literal["terminal", "resume", "status", "recovery"]
    finalized_at: datetime
    recovered: bool = False
    source_event_count: int = Field(default=0, ge=0)
    terminal_error_type: str | None = Field(default=None, max_length=256)


def collect_resource_budgets(plan: Plan) -> list[ResourceBudgetSpec]:
    """Return authored budgets plus deterministic legacy guard projections."""
    authored = getattr(plan, "resource_budgets", ())
    budgets = [budget.model_copy(deep=True) for budget in authored]
    for step in plan.steps:
        if step.token_budget is not None:
            budgets.append(
                _legacy_budget(
                    plan=plan,
                    step_id=step.step_id,
                    metric=BudgetMetric.TOTAL_TOKENS,
                    threshold=step.token_budget,
                )
            )
        if step.max_budget_usd is not None:
            budgets.append(
                _legacy_budget(
                    plan=plan,
                    step_id=step.step_id,
                    metric=BudgetMetric.LIST_COST_USD,
                    threshold=step.max_budget_usd,
                )
            )
    ids = [budget.budget_id for budget in budgets]
    if len(ids) != len(set(ids)):
        raise ValueError("resource budget IDs must be unique after legacy projection")
    return budgets


def _legacy_budget(
    *,
    plan: Plan,
    step_id: str,
    metric: BudgetMetric,
    threshold: float,
) -> ResourceBudgetSpec:
    identity = f"{plan.process}\x1f{step_id}\x1f{metric.value}"
    return ResourceBudgetSpec(
        budget_id=derive_typed_id_from_key("bud", identity),
        scope=BudgetScope(kind=BudgetScopeKind.STEP, key=step_id),
        metric=metric,
        threshold=threshold,
        posture=BudgetPosture.OBSERVE,
        source="legacy",
        description=f"Projected existing {metric.value} guard for step {step_id}",
    )


def evaluate_resource_budgets(
    document: ResourcesDocument,
    budgets: Sequence[ResourceBudgetSpec],
    *,
    events: Sequence[ResourceEvent] = (),
) -> list[BudgetEvaluation]:
    """Evaluate budgets from one reconciled resource projection."""
    return [_evaluate_budget(document, budget, events=events) for budget in budgets]


def _evaluate_budget(
    document: ResourcesDocument,
    budget: ResourceBudgetSpec,
    *,
    events: Sequence[ResourceEvent],
) -> BudgetEvaluation:
    if budget.meter is not None:
        coverage, actual, estimated = _meter_observation(document, budget, events=events)
    else:
        coverage, actual, estimated = _metric_observation(document, budget, events=events)
    status = _budget_status(
        coverage=coverage,
        actual=actual,
        estimated=estimated,
        threshold=budget.threshold,
        near_ratio=budget.near_ratio,
    )
    observed = actual if actual is not None else estimated
    target = budget.metric.value if budget.metric is not None else _format_meter(budget)
    message = (
        f"{target} is unmeasured for {budget.scope.kind.value} scope"
        if coverage is BudgetCoverage.UNMEASURED or observed is None
        else f"{target}={observed:g}; threshold={budget.threshold:g}; coverage={coverage.value}"
    )
    return BudgetEvaluation(
        budget=budget,
        status=status,
        coverage=coverage,
        actual_quantity=actual,
        estimated_quantity=estimated,
        message=message,
    )


def _meter_observation(
    document: ResourcesDocument,
    budget: ResourceBudgetSpec,
    *,
    events: Sequence[ResourceEvent],
) -> tuple[BudgetCoverage, float | None, float | None]:
    if budget.meter is None:
        raise AssertionError("meter observation requires a meter target")
    if budget.scope.kind is BudgetScopeKind.RUN:
        rollups: Sequence[MeterRollup] = document.meter_rollups
    elif budget.scope.kind in {
        BudgetScopeKind.PROVIDER,
        BudgetScopeKind.PRODUCT,
        BudgetScopeKind.MODEL,
    }:
        quantities = [
            quantity
            for event in events
            if _event_matches_scope(event, budget.scope)
            for quantity in event.meters
            if quantity.key.sort_key() == budget.meter.sort_key()
        ]
        if not quantities or any(
            quantity.coverage.value == "unmeasured" for quantity in quantities
        ):
            return (BudgetCoverage.UNMEASURED, None, None)
        actual = sum(quantity.actual_quantity or 0 for quantity in quantities)
        estimated = sum(quantity.estimated_quantity or 0 for quantity in quantities)
        if any(quantity.coverage.value == "estimated" for quantity in quantities):
            return (BudgetCoverage.ESTIMATED, None, actual + estimated)
        return (BudgetCoverage.MEASURED, actual, None)
    elif budget.scope.kind in {
        BudgetScopeKind.PROCESS,
        BudgetScopeKind.STEP,
        BudgetScopeKind.TOOL,
    }:
        nodes = _find_nodes(document.hierarchy_root, budget.scope)
        if not nodes:
            return (BudgetCoverage.UNMEASURED, None, None)
        matches_by_node = [
            [
                rollup
                for rollup in node.total_meters
                if rollup.key.sort_key() == budget.meter.sort_key()
            ]
            for node in nodes
        ]
        if any(len(matches) != 1 for matches in matches_by_node):
            return (BudgetCoverage.UNMEASURED, None, None)
        return _combine_meter_observations([matches[0] for matches in matches_by_node])
    else:
        rollups = ()
    matches = [rollup for rollup in rollups if rollup.key.sort_key() == budget.meter.sort_key()]
    if len(matches) != 1:
        return (BudgetCoverage.UNMEASURED, None, None)
    rollup = matches[0]
    if rollup.coverage.value == "unmeasured":
        return (BudgetCoverage.UNMEASURED, None, None)
    if rollup.coverage.value == "estimated":
        estimate = (rollup.actual_quantity or 0) + (rollup.estimated_quantity or 0)
        return (BudgetCoverage.ESTIMATED, None, estimate)
    return (BudgetCoverage.MEASURED, rollup.actual_quantity, None)


def _combine_meter_observations(
    rollups: Sequence[MeterRollup],
) -> tuple[BudgetCoverage, float | None, float | None]:
    if any(rollup.coverage.value == "unmeasured" for rollup in rollups):
        return (BudgetCoverage.UNMEASURED, None, None)
    actual = sum(rollup.actual_quantity or 0 for rollup in rollups)
    estimated = sum(rollup.estimated_quantity or 0 for rollup in rollups)
    if any(rollup.coverage.value == "estimated" for rollup in rollups):
        return (BudgetCoverage.ESTIMATED, None, actual + estimated)
    return (BudgetCoverage.MEASURED, actual, None)


def _metric_observation(
    document: ResourcesDocument,
    budget: ResourceBudgetSpec,
    *,
    events: Sequence[ResourceEvent],
) -> tuple[BudgetCoverage, float | None, float | None]:
    if budget.metric is None:
        raise AssertionError("metric observation requires a metric target")
    if budget.scope.kind is BudgetScopeKind.RUN:
        return _metric_from_metrics(document.hierarchy_root.total_metrics, budget.metric)
    if budget.scope.kind in {
        BudgetScopeKind.PROCESS,
        BudgetScopeKind.STEP,
        BudgetScopeKind.TOOL,
    }:
        nodes = _find_nodes(document.hierarchy_root, budget.scope)
        if not nodes:
            return (BudgetCoverage.UNMEASURED, None, None)
        observations = [_metric_from_metrics(node.total_metrics, budget.metric) for node in nodes]
        if any(row[0] is BudgetCoverage.UNMEASURED for row in observations):
            return (BudgetCoverage.UNMEASURED, None, None)
        actual = sum(row[1] or 0 for row in observations)
        estimated = sum(row[2] or 0 for row in observations)
        if any(row[0] is BudgetCoverage.ESTIMATED for row in observations):
            return (BudgetCoverage.ESTIMATED, None, actual + estimated)
        return (BudgetCoverage.MEASURED, actual, None)
    matching = [event for event in events if _event_matches_scope(event, budget.scope)]
    observed = [
        row
        for event in matching
        if (row := _metric_from_metrics(event.metrics, budget.metric))[0]
        is not BudgetCoverage.UNMEASURED
    ]
    if not observed:
        return (BudgetCoverage.UNMEASURED, None, None)
    actual = sum(row[1] or 0 for row in observed) if observed else None
    estimated = sum(row[2] or 0 for row in observed) if observed else None
    if any(row[0] is BudgetCoverage.ESTIMATED for row in observed):
        return (BudgetCoverage.ESTIMATED, None, (actual or 0) + (estimated or 0))
    return (BudgetCoverage.MEASURED, actual, None)


def _metric_from_metrics(
    metrics: Metrics,
    metric: BudgetMetric,
) -> tuple[BudgetCoverage, float | None, float | None]:
    if metric is BudgetMetric.TOTAL_TOKENS:
        fields = (
            metrics.input_tokens,
            metrics.output_tokens,
            metrics.cache_read_tokens,
            metrics.cache_write_tokens,
        )
        if any(value is None for value in fields):
            return (BudgetCoverage.UNMEASURED, None, None)
        return (BudgetCoverage.MEASURED, float(sum(cast(int, value) for value in fields)), None)
    value = cast(float | int | None, getattr(metrics, metric.value, None))
    if value is None:
        return (BudgetCoverage.UNMEASURED, None, None)
    if metric is BudgetMetric.LIST_COST_USD:
        return (BudgetCoverage.ESTIMATED, None, float(value))
    return (BudgetCoverage.MEASURED, float(value), None)


def _find_nodes(root: Node, scope: BudgetScope) -> list[Node]:
    id_matches: list[Node] = []
    label_matches: list[Node] = []

    def walk(node: Node) -> None:
        if node.node_type == scope.kind.value:
            if node.node_id == scope.key:
                id_matches.append(node)
            elif node.label == scope.key:
                label_matches.append(node)
        for child in node.children:
            walk(child)

    walk(root)
    if len(id_matches) == 1:
        return id_matches
    if scope.kind is BudgetScopeKind.TOOL:
        return label_matches
    return label_matches if len(label_matches) == 1 else []


def _event_matches_scope(event: ResourceEvent, scope: BudgetScope) -> bool:
    if event.provider is None or scope.key is None:
        return False
    if scope.kind is BudgetScopeKind.PROVIDER:
        return event.provider.provider == scope.key
    if scope.kind is BudgetScopeKind.PRODUCT:
        return event.provider.product == scope.key
    if scope.kind is BudgetScopeKind.MODEL:
        return event.provider.model == scope.key
    return False


def _budget_status(
    *,
    coverage: BudgetCoverage,
    actual: float | None,
    estimated: float | None,
    threshold: float,
    near_ratio: float,
) -> BudgetStatus:
    if coverage is BudgetCoverage.UNMEASURED:
        return BudgetStatus.UNMEASURED
    observed = actual if actual is not None else estimated
    if observed is None:
        return BudgetStatus.UNMEASURED
    if threshold == 0:
        return BudgetStatus.EXCEEDED if observed > 0 else BudgetStatus.WITHIN
    if observed >= threshold:
        return BudgetStatus.EXCEEDED
    return BudgetStatus.NEAR if observed >= threshold * near_ratio else BudgetStatus.WITHIN


def _format_meter(budget: ResourceBudgetSpec) -> str:
    return "meter" if budget.meter is None else "/".join(budget.meter.sort_key())
