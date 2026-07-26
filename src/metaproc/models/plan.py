"""Resolved plan models — fully resolved execution plans.

After plan building, all variables are substituted, adapters merged,
and fan-out items discovered. These models represent that resolved state.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from metaproc.models.authored import IOSpec, ParseConfig, RetryPolicy, ValueType
from metaproc.models.lane import ExecutionLane, LaneMatrix


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
    - ``metaproc:Plan/0.5`` (current): adds ``lane_matrix`` and
      ``execution_lanes`` to the public surface (upstream change). Single-lane
      and lane-less plans still validate against this version.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    historical_schema_tokens: ClassVar[tuple[str, ...]] = ("metaproc:Plan/0.4",)

    schema_: str = Field(default="metaproc:Plan/0.5", alias="schema")
    generated_at: str = ""
    process: str = ""
    params: dict[str, str] = Field(default_factory=dict)
    execution_profile: str | None = None
    artifact_namespace: str | None = None
    deps: dict[str, ResolvedDep] = Field(default_factory=dict)
    steps: list[ResolvedStep] = Field(default_factory=list)
    lane_matrix: LaneMatrix | None = None
    execution_lanes: list[ExecutionLane] = Field(default_factory=list)


class PlanEnvelope(BaseModel):
    """Envelope for resolved plan documents (``plan:`` key)."""

    plan: Plan
