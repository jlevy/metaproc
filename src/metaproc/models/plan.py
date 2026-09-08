"""Resolved plan models — fully resolved execution plans.

After plan building, all variables are substituted, adapters merged,
and fan-out items discovered. These models represent that resolved state.
"""

from __future__ import annotations

from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metaproc.models.authored import IOSpec, ParseConfig, RetryPolicy, ValueType
from metaproc.models.lane import ExecutionLane, LaneMatrix
from metaproc.models.resource_budget import ResourceBudgetSpec

RUN_PLAN_SNAPSHOT_CONTRACT = "metaproc:RunPlanSnapshot/0.1"


class ResolvedAdapter(BaseModel):
    """Typed wrapper around the resolved adapter config.

    Preserves the two-level ``{type, config}`` shape produced by
    :func:`metaproc.engine.build_plan.merge_defaults` — the inner ``config``
    is a flat dict of adapter-specific keys (model/provider/tools/timeout/etc.)
    whose shape varies by adapter type. The VizModel ``AdapterSummary``
    flattens interesting keys for display; the raw config stays opaque here.
    """

    type: str
    config: dict[str, object] = Field(default_factory=dict)


class FanOut(BaseModel):
    """Resolved fan-out: items discovered from source at plan time."""

    over: str
    bind: str
    source: str
    bind_fields: list[str] = Field(default_factory=list)
    batch_size: int = 10
    items: list[dict[str, str]] = Field(default_factory=list)
    filtered_count: int = 0
    retry: RetryPolicy | None = None
    align: Literal["same_key"] | None = None
    max_concurrency: int | None = None


class ResolvedStep(BaseModel):
    """A fully resolved step in a plan — all variables substituted."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    step_id: str
    mode: Literal["code", "agent", "composite", "manual"]
    description: str = ""
    adapter: ResolvedAdapter = Field(
        default_factory=lambda: ResolvedAdapter(type="claude-code-cli")
    )
    resources: dict[str, object] = Field(default_factory=dict)
    prompt_prefix: str | None = None
    prompt_paths: list[str] = Field(default_factory=list)
    reuse_policy: str | None = None
    fan_out: FanOut | None = None
    handler: str | None = None
    command: str | None = None
    with_: dict[str, str] = Field(default_factory=dict, alias="with")
    inputs: dict[str, IOSpec] = Field(default_factory=dict)
    outputs: dict[str, IOSpec] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    needs: list[str] = Field(default_factory=list)
    on_failure: Literal["block", "continue"] = "block"
    uses_path: str | None = None
    produced_refs: list[str] = Field(default_factory=list)
    """Referenced runbook paths that another step in this plan writes during the run.

    Resolved by ``build_plan`` from the deps a ``prompt_paths`` or ``uses`` entry
    points at, and read by ``fingerprint_step``, which excludes their bytes from
    the step fingerprint. Carrying the set on the resolved step rather than
    passing it per call is what keeps every fingerprint of a given step equal:
    the plan-time and execution-time hashes must be comparable, and two callers
    disagreeing about the set would silently produce two different hashes.
    """
    execution_profile: str | None = None
    artifact_namespace: str | None = None
    variant: str | None = None
    output_root: str | None = None
    max_budget_usd: float | None = None
    token_budget: int | None = None


class ResolvedDep(BaseModel):
    """A resolved process-level dependency."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    path: str
    produced_by: str | None = None
    consumers: list[str] = Field(default_factory=list)
    state: str | None = None
    as_: ValueType | None = Field(default=None, alias="as")
    parse: ParseConfig | None = None


class Plan(BaseModel):
    """Resolved execution plan: all variables substituted, adapters merged.

    Schema-token history
    --------------------
    - ``metaproc:Plan/0.4`` (pre-lanes): no ``lane_matrix`` or
      ``execution_lanes`` fields. Still resolvable because the new
      fields are optional, but plans written under 0.4 cannot describe
      a multi-lane shape.
    - ``metaproc:Plan/0.5``: adds ``lane_matrix`` and ``execution_lanes``.
    - ``metaproc:Plan/0.6`` (current): adds reporting-only
      ``resource_budgets``. Plans without budgets still validate.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    historical_schema_tokens: ClassVar[tuple[str, ...]] = (
        "metaproc:Plan/0.4",
        "metaproc:Plan/0.5",
    )

    schema_: str = Field(default="metaproc:Plan/0.6", alias="schema")
    generated_at: str = ""
    process: str = ""
    params: dict[str, str] = Field(default_factory=dict)
    execution_profile: str | None = None
    artifact_namespace: str | None = None
    deps: dict[str, ResolvedDep] = Field(default_factory=dict)
    resource_budgets: list[ResourceBudgetSpec] = Field(default_factory=list)
    steps: list[ResolvedStep] = Field(default_factory=list)
    lane_matrix: LaneMatrix | None = None
    execution_lanes: list[ExecutionLane] = Field(default_factory=list)


class PlanEnvelope(BaseModel):
    """Envelope for resolved plan documents (``plan:`` key)."""

    plan: Plan


class RunPlanStep(BaseModel):
    """Non-sensitive projection authority for one resolved runtime step."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    mode: Literal["code", "agent", "composite", "manual"]
    task_shape: Literal["scalar", "mapped"]
    item_keys: list[str] | None
    outputs: dict[str, IOSpec] = Field(default_factory=dict)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{16}$")

    @model_validator(mode="after")
    def _valid_item_key_set(self) -> Self:
        if self.item_keys is None:
            return self
        if self.task_shape == "scalar" and self.item_keys:
            raise ValueError("scalar run-plan steps cannot declare item keys")
        if len(self.item_keys) != len(set(self.item_keys)):
            raise ValueError("run-plan item keys must be unique")
        from metaproc.paths import (  # noqa: PLC0415 -- avoids the models/paths import cycle
            is_safe_item_key,
        )

        if invalid := [key for key in self.item_keys if not is_safe_item_key(key)]:
            raise ValueError(f"run-plan item keys must be safe path components: {invalid!r}")
        return self


class RunPlanSnapshot(BaseModel):
    """Minimal exact plan projection for one runtime scope."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra="forbid")

    schema_: Literal["metaproc:RunPlanSnapshot/0.1"] = Field(
        default=RUN_PLAN_SNAPSHOT_CONTRACT, alias="schema"
    )
    run_id: str = Field(min_length=1)
    scope_path: list[str] = Field(default_factory=list)
    steps: list[RunPlanStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exact_step_set(self) -> Self:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("run-plan steps must have unique step IDs")
        if any(step.item_keys is None for step in self.steps):
            raise ValueError("recorded run-plan steps must declare exact item keys")
        return self
