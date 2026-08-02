"""Common resource-budget contracts and deterministic evaluation.

Budgets are reporting and guard-policy inputs, not a second accounting ledger.
They evaluate the same ``ResourcesDocument`` tree and exact provider meters that
Metaproc already derives from typed resource events. Existing adapter-level
``token_budget`` and ``max_budget_usd`` enforcement remains unchanged.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from metaproc.ids import new_typed_id, require_typed_id

if TYPE_CHECKING:
    from metaproc.models.plan import Plan
    from metaproc.models.resources import (
        MeterRollup,
        Metrics,
        Node,
        ResourceEvent,
        ResourcesDocument,
    )

_DERIVED_BUDGET_DIGEST_BYTES = 20


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
    """Declared operator response to a budget state.

    The common evaluator records this posture. It does not add a new dispatch
    refusal path; existing legacy adapter guards continue enforcing their current
    limits, while common refusal remains a future opt-in after meter coverage is
    proven.
    """

    OBSERVE = "observe"
    WARN = "warn"
    REFUSE_NEW_WORK = "refuse-new-work"


class BudgetScopeKind(StrEnum):
    """Hierarchy or provider slice selected by a budget."""

    RUN = "run"
    STEP = "step"
    PROVIDER = "provider"
    MODEL = "model"
    TOOL = "tool"


class BudgetMetric(StrEnum):
    """Canonical resource metric or explicitly unsupported future metric."""

    WALL_TIME_S = "wall_time_s"
    ACTIVE_CPU_S = "active_cpu_s"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    CACHE_READ_TOKENS = "cache_read_tokens"
    CACHE_WRITE_TOKENS = "cache_write_tokens"
    TOTAL_TOKENS = "total_tokens"
    ACTUAL_COST_USD = "actual_cost_usd"
    LIST_COST_USD = "list_cost_usd"
    MODEL_TURNS = "model_turns"
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
    BudgetMetric.MODEL_TURNS: "count",
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
    """One run, hierarchy node, or provider/model slice."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: BudgetScopeKind = BudgetScopeKind.RUN
    key: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_scope_key(self) -> Self:
        if self.kind is BudgetScopeKind.RUN and self.key is not None:
            raise ValueError("run budget scope must not provide a key")
        if self.kind is not BudgetScopeKind.RUN and self.key is None:
            raise ValueError(f"{self.kind.value} budget scope requires a key")
        return self


class BudgetMeterKey(BaseModel):
    """Exact provider-meter selector used by an authored budget.

    This wire model intentionally mirrors ``resources.MeterKey`` without
    importing it at module import time, keeping authored models independent of
    the resource-document module and avoiding a circular Pydantic dependency.
    Evaluation compares the exact four-part tuple.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=128)
    product: str = Field(min_length=1, max_length=128)
    meter: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=64)

    @field_validator("provider", "product", "meter", "unit")
    @classmethod
    def _reject_unsafe_key_text(cls, value: str) -> str:
        if value != value.strip() or any(char.isspace() for char in value):
            raise ValueError("budget meter-key components must be tokens without whitespace")
        return value

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.provider, self.product, self.meter, self.unit)


class ResourceBudgetSpec(BaseModel):
    """One immutable threshold over a canonical metric or exact meter."""

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
        if (
            self.scope.kind is BudgetScopeKind.PROVIDER
            and self.meter is not None
            and self.scope.key != self.meter.provider
        ):
            raise ValueError("provider-scoped meter budget must match the meter provider")
        if self.metric is not None:
            expected_unit = _METRIC_UNITS[self.metric]
        else:
            if self.meter is None:
                raise AssertionError("validated meter budget lost its target")
            expected_unit = self.meter.unit
        if self.unit is None:
            self.unit = expected_unit
        elif self.unit != expected_unit:
            raise ValueError(
                f"budget unit {self.unit!r} does not match target unit {expected_unit!r}"
            )
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


class FinalizationState(StrEnum):
    """Terminal state retained on the resource report."""

    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ResourceFinalization(BaseModel):
    """How and when a terminal resource projection was produced."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    state: FinalizationState
    trigger: Literal["terminal", "resume", "status", "rehydration"]
    finalized_at: datetime
    recovered: bool = False
    source_event_count: int = Field(default=0, ge=0)
    terminal_error_type: str | None = Field(default=None, max_length=256)


class ResourceUsageSummary(BaseModel):
    """Soft-schema frontmatter for ``resource-usage-summary.md``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["metaproc:ResourceUsageSummary/0.1"] = Field(
        default="metaproc:ResourceUsageSummary/0.1",
        alias="schema",
    )
    usage_id: str
    run_id: str
    generated_at: datetime
    resource_schema: str
    finalization: ResourceFinalization
    metrics: dict[str, float | int | None]
    provider_meters: list[dict[str, object]] = Field(default_factory=list)
    budget_evaluations: list[BudgetEvaluation] = Field(default_factory=list)

    @field_validator("usage_id")
    @classmethod
    def _validate_usage_id(cls, value: str) -> str:
        return require_typed_id(value, "use")


def collect_resource_budgets(plan: Plan) -> list[ResourceBudgetSpec]:
    """Return authored budgets plus deterministic projections of legacy step guards."""
    budgets = [budget.model_copy(deep=True) for budget in plan.resource_budgets]
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
                    metric=BudgetMetric.ACTUAL_COST_USD,
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
    digest = hashlib.sha256(identity.encode()).digest()
    suffix = base64.b32encode(digest[:_DERIVED_BUDGET_DIGEST_BYTES]).decode().lower().rstrip("=")
    return ResourceBudgetSpec(
        budget_id=new_typed_id("bud", unique_suffix=suffix),
        scope=BudgetScope(kind=BudgetScopeKind.STEP, key=step_id),
        metric=metric,
        threshold=threshold,
        posture=BudgetPosture.REFUSE_NEW_WORK,
        source="legacy",
        description=f"Projected existing {metric.value} guard for step {step_id}",
    )


def evaluate_resource_budgets(
    document: ResourcesDocument,
    budgets: Sequence[ResourceBudgetSpec],
    *,
    events: Sequence[ResourceEvent] = (),
) -> list[BudgetEvaluation]:
    """Evaluate budgets from the deduplicated resource projection and optional events."""
    return [_evaluate_budget(document, budget, events=events) for budget in budgets]


def _evaluate_budget(
    document: ResourcesDocument,
    budget: ResourceBudgetSpec,
    *,
    events: Sequence[ResourceEvent],
) -> BudgetEvaluation:
    if budget.meter is not None:
        coverage, actual, estimated = _meter_observation(document, budget)
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
    if observed is None:
        message = f"{target} is unmeasured for {budget.scope.kind.value} scope"
    else:
        message = (
            f"{target}={observed:g}; threshold={budget.threshold:g}; coverage={coverage.value}"
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
) -> tuple[BudgetCoverage, float | None, float | None]:
    if budget.meter is None:
        raise AssertionError("meter observation requires a meter target")
    rollups: Sequence[MeterRollup]
    if budget.scope.kind is BudgetScopeKind.RUN:
        rollups = document.meter_rollups
    elif budget.scope.kind in (BudgetScopeKind.STEP, BudgetScopeKind.TOOL):
        node = _find_node(document.hierarchy_root, budget.scope)
        rollups = node.total_meters if node is not None else ()
    elif budget.scope.kind is BudgetScopeKind.PROVIDER:
        if budget.scope.key != budget.meter.provider:
            return (BudgetCoverage.UNMEASURED, None, None)
        rollups = document.meter_rollups
    else:
        return (BudgetCoverage.UNMEASURED, None, None)
    matches = [rollup for rollup in rollups if rollup.key.sort_key() == budget.meter.sort_key()]
    if len(matches) != 1:
        return (BudgetCoverage.UNMEASURED, None, None)
    rollup = matches[0]
    return (
        BudgetCoverage(rollup.coverage.value),
        rollup.actual_quantity,
        rollup.estimated_quantity,
    )


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
    if budget.scope.kind in (BudgetScopeKind.STEP, BudgetScopeKind.TOOL):
        node = _find_node(document.hierarchy_root, budget.scope)
        return (
            _metric_from_metrics(node.total_metrics, budget.metric)
            if node is not None
            else (BudgetCoverage.UNMEASURED, None, None)
        )
    matching_events = [event for event in events if _event_matches_scope(event, budget.scope)]
    known: list[float] = []
    for event in matching_events:
        coverage, actual, estimated = _metric_from_metrics(event.metrics, budget.metric)
        _ = coverage
        value = actual if actual is not None else estimated
        if value is not None:
            known.append(value)
    if not known:
        return (BudgetCoverage.UNMEASURED, None, None)
    coverage = (
        BudgetCoverage.ESTIMATED
        if budget.metric is BudgetMetric.LIST_COST_USD
        else BudgetCoverage.MEASURED
    )
    total = sum(known)
    return (
        (coverage, None, total) if coverage is BudgetCoverage.ESTIMATED else (coverage, total, None)
    )


def _metric_from_metrics(
    metrics: Metrics,
    metric: BudgetMetric,
) -> tuple[BudgetCoverage, float | None, float | None]:
    if metric is BudgetMetric.MODEL_TURNS:
        return (BudgetCoverage.UNMEASURED, None, None)
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


def _find_node(root: Node, scope: BudgetScope) -> Node | None:
    matches: list[Node] = []
    wanted_type = scope.kind.value

    def walk(node: Node) -> None:
        if node.node_type == wanted_type and (node.node_id == scope.key or node.label == scope.key):
            matches.append(node)
        for child in node.children:
            walk(child)

    walk(root)
    return matches[0] if len(matches) == 1 else None


def _event_matches_scope(event: ResourceEvent, scope: BudgetScope) -> bool:
    if event.provider is None or scope.key is None:
        return False
    if scope.kind is BudgetScopeKind.PROVIDER:
        return event.provider.provider == scope.key
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
    observed = actual if actual is not None else estimated
    if observed is not None and observed >= threshold:
        return BudgetStatus.EXCEEDED
    if coverage is BudgetCoverage.UNMEASURED or observed is None:
        return BudgetStatus.UNMEASURED
    if threshold == 0:
        return BudgetStatus.WITHIN
    return BudgetStatus.NEAR if observed >= threshold * near_ratio else BudgetStatus.WITHIN


def _format_meter(budget: ResourceBudgetSpec) -> str:
    if budget.meter is None:
        return "meter"
    return "/".join(budget.meter.sort_key())
