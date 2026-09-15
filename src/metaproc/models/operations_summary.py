"""Typed frontmatter contract for the run operations summary.

Every figure the summary cannot establish is ``null`` with a reason in the owning
section's ``unavailable`` map, and a validator enforces that pairing, so a missing
measurement never reads as a zero. The frontmatter writer omits null keys, so a figure
absent from the YAML is null and its reason is still present.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.config import JsonDict

from metaproc.models.resources import CoverageState, MeterKey, UnpricedModel

OPERATIONS_SUMMARY_CONTRACT = "metaproc.operations:AgentOperationsSummary/v1"
OPERATIONS_SUMMARY_ENVELOPE = "agent_operations"
OPERATIONS_SUMMARY_EXTRACTOR_VERSION = 2


def _close_nullable_model_references(schema: JsonDict) -> None:
    """State closure on each ``Model | None`` property of one record's JSON Schema.

    Pydantic renders such a property as ``anyOf: [{$ref: Model}, {type: null}]``. The
    softschema enforced profile infers ``unevaluatedProperties: false`` on that wrapper,
    then refuses any nullable reference whose target contains an inferred closure
    (``composition_reference_context``), which is every section holding a nullable model.
    Every model this contract references already forbids extra keys, so stating the
    closure leaves validation unchanged. It is ``unevaluatedProperties`` because the
    wrapper declares no properties of its own, so ``additionalProperties: false`` would
    reject every key.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    for property_schema in properties.values():
        if not isinstance(property_schema, dict):
            continue
        branches = property_schema.get("anyOf")
        if (
            isinstance(branches, list)
            and len(branches) == 2
            and {"type": "null"} in branches
            and any(isinstance(branch, dict) and "$ref" in branch for branch in branches)
        ):
            property_schema["unevaluatedProperties"] = False


class _Explained(BaseModel):
    """A record whose null figures each carry a reason in ``unavailable``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", json_schema_extra=_close_nullable_model_references
    )

    unavailable: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _nulls_are_explained(self) -> Self:
        fields = type(self).model_fields
        for name in fields:
            if name == "unavailable":
                continue
            if getattr(self, name) is None and name not in self.unavailable:
                raise ValueError(f"{name} is null without a reason in unavailable")
        for name, reason in self.unavailable.items():
            if name not in fields or name == "unavailable":
                raise ValueError(f"unavailable names an unknown field: {name}")
            if getattr(self, name) is not None:
                raise ValueError(f"{name} carries both a value and an unavailable reason")
            if not reason.strip():
                raise ValueError(f"unavailable reason for {name} is blank")
        return self


class Distribution(BaseModel):
    """Order statistics over a non-empty sample, in seconds unless the field says otherwise.

    Percentiles interpolate linearly between closest ranks.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    count: int = Field(ge=1)
    p50: float
    p90: float
    max: float
    mean: float
    total: float


class RunFigures(_Explained):
    """Identity, terminal state, and real elapsed time of the run root."""

    process: str | None = None
    state: str | None = None
    state_source: Literal["finalization", "resource_summary", "process_status"] | None = None
    started_at: str | None = None
    ended_at: str | None = None
    elapsed_s: float | None = None
    item_count: int | None = None
    variant: str | None = None
    execution_profile: str | None = None
    backend: str | None = None
    revisions: dict[str, str] | None = None


class SetupFigures(_Explained):
    """Time spent in top-level steps that are not per-item fan-outs."""

    step_ids: list[str] = Field(default_factory=list)
    elapsed_s: float | None = None
    share_of_elapsed: float | None = None


class StageRow(_Explained):
    """One top-level step of the run root."""

    step_id: str
    mode: str | None = None
    task_shape: Literal["scalar", "mapped"] | None = None
    state: str | None = None
    item_count: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_s: float | None = None
    share_of_elapsed: float | None = None


class StepRef(BaseModel):
    """The slowest direct step inside one item's stage scope."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    stage: str
    step_id: str
    elapsed_s: float


class ItemStage(_Explained):
    """One item's pass through one mapped top-level stage.

    ``wait_before_s`` is the time from the completion of the stage this item started
    before this one to this stage's start. Stages without an edge between them can
    overlap; a stage that started before the previous one completed has no wait.
    """

    stage: str
    state: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    running_s: float | None = None
    wait_before_s: float | None = None
    slowest_step: StepRef | None = None


class ItemChain(_Explained):
    """One item key's path through every mapped top-level stage it appears in.

    ``stages`` are in start order, with stages that never started last in plan order.
    ``barrier_wait_s`` sums the waits between consecutive stages and is null when any two
    of them overlap.
    """

    item_key: str
    stages: list[ItemStage] = Field(default_factory=list)
    chain_running_s: float | None = None
    barrier_wait_s: float | None = None
    chain_span_s: float | None = None
    slowest_step: StepRef | None = None


class StageStepStats(BaseModel):
    """Distribution of one direct child step across a stage's item scopes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    step_id: str
    mode: str | None = None
    elapsed_s: Distribution


class StageItemStats(_Explained):
    """Per-item running time within one mapped stage."""

    stage: str
    item_count: int
    running_s: Distribution | None = None
    steps: list[StageStepStats] = Field(default_factory=list)


class ItemFigures(_Explained):
    """Per-item chain time across the mapped top-level stages."""

    stages: list[str] = Field(default_factory=list)
    item_count: int
    chain_running_s: Distribution | None = None
    barrier_wait_s: Distribution | None = None
    chain_span_s: Distribution | None = None
    per_stage: list[StageItemStats] = Field(default_factory=list)
    slowest: list[ItemChain] = Field(default_factory=list)
    items: list[ItemChain] = Field(default_factory=list)


class StepTypeRow(BaseModel):
    """One step type, keyed by owning process and step id, across every scope."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    process: str
    step_id: str
    mode: str | None = None
    elapsed_s: Distribution


class StepFigures(_Explained):
    """Step durations from every scope's process status and task status."""

    scope_count: int
    ignored_state_dirs: int
    """Directories shaped like a scope whose run plan is absent or names another path."""
    unreadable_plans: dict[str, str] = Field(default_factory=dict)
    """Run plans present but unreadable, by path, with the error; their scopes still count."""
    rows: list[StepTypeRow] = Field(default_factory=list)
    agent_total_s: float | None = None
    code_total_s: float | None = None


class PoolRow(_Explained):
    """Concurrency observed by one RunPool event and health stream pair.

    Sample shares come from the pool's health samples, or from its ``pressure_check``
    events when it wrote no health stream; ``sample_source`` names which.
    """

    source: str
    ceiling: int | None = None
    process_starts: int
    peak_running: int | None = None
    mean_running: float | None = None
    health_samples: int
    pressure_checks: int = 0
    sample_source: Literal["health_sample", "pressure_check"] | None = None
    share_samples_at_cap: float | None = None
    share_samples_at_ceiling: float | None = None
    cap_min: int | None = None
    cap_max: int | None = None


class ParallelismFigures(_Explained):
    """Concurrently running pooled tasks against the pool ceiling.

    ``peak_running`` and ``mean_running`` replay ``process_start`` and ``process_exit``
    events; ``mean_running`` is time-weighted over the pool window from its first start to
    its last exit. Sample shares come from health samples, or pressure checks when a
    pool wrote no health stream: at the cap means ``active_count`` reached the current
    concurrency cap, at the ceiling means it reached the pool's configured maximum.

    ``pools`` holds the streams that started a pool or a process. Every agent step also
    owns an admission stream that records only authentication and host admission;
    ``empty_streams`` counts those and any other stream that started neither.
    """

    ceiling: int | None = None
    peak_running: int | None = None
    mean_running: float | None = None
    health_samples: int
    pressure_checks: int = 0
    sample_source: Literal["health_sample", "pressure_check"] | None = None
    share_samples_at_cap: float | None = None
    share_samples_at_ceiling: float | None = None
    pools: list[PoolRow] = Field(default_factory=list)
    empty_streams: int = 0


class RetryStepRow(BaseModel):
    """Attempt counts for one step type, keyed by owning process, with a non-succeeded attempt."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    process: str
    step_id: str
    attempts: int
    not_succeeded: int
    by_failure_class: dict[str, int] = Field(default_factory=dict)


class RetryFigures(_Explained):
    """``TaskAttemptRecord`` attempts found by schema token in every scope.

    A non-succeeded attempt without ``failure_class``, lost attempts included, counts
    under ``unclassified``. ``wrote_nothing`` counts terminal attempts whose recorded
    output failures are all ``missing``. ``retries`` counts attempt records beyond the
    first for each task, where a task is one step, or one item of a step, in one scope.
    """

    attempt_records: int
    retries: int = 0
    by_disposition: dict[str, int] = Field(default_factory=dict)
    by_failure_class: dict[str, int] = Field(default_factory=dict)
    live_attempts: int
    wrote_nothing: int | None = None
    by_step: list[RetryStepRow] = Field(default_factory=list)
    path_shapes: dict[str, int] = Field(default_factory=dict)
    unreadable_records: int
    non_record_attempt_files: int
    """``attempt.yaml`` files without the schema token, such as legacy launch snapshots."""


class MeterFigure(BaseModel):
    """A provider meter copied from the resource usage summary, coverage included."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    key: MeterKey
    coverage: CoverageState
    actual_quantity: float | None = Field(default=None, ge=0)
    estimated_quantity: float | None = Field(default=None, ge=0)
    unmeasured_event_count: int = Field(default=0, ge=0)


class TokenFigures(_Explained):
    """Token totals copied from the resource usage summary."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


class AgentFigures(_Explained):
    """Agent invocations read from transcript terminal results and invocation records.

    ``tool_results_at_cap`` counts transcript lines of at least 16 MiB encoded bytes that
    carry a tool result. Only transcripts at least that large are scanned, in bounded
    chunks.
    """

    transcripts: int
    transcripts_without_result: int
    provider_s: float | None = None
    requested_models: dict[str, int] | None = None
    served_models: dict[str, int] | None = None
    model_mismatches: int | None = None
    tokens: TokenFigures | None = None
    meters: list[MeterFigure] = Field(default_factory=list)
    tool_calls: int | None = None
    tool_results_at_cap: int | None = None
    oversized_transcripts: list[str] = Field(default_factory=list)


class ResourceFigures(_Explained):
    """Machine resources from the resource usage summary and RunPool health samples.

    ``unpriced_models`` is copied from the resource usage summary: invocations with token
    usage whose model has no list price, which ``list_cost_usd`` leaves out. When it is
    nonempty, ``list_cost_usd`` is a lower bound.
    """

    resource_finalization_state: str | None = None
    list_cost_usd: float | None = None
    unpriced_models: list[UnpricedModel] = Field(default_factory=list)
    actual_cost_usd: float | None = None
    cpu_pct_avg: float | None = None
    cpu_pct_max: float | None = None
    rss_bytes_max: int | None = None
    health_samples: int
    swap_used_peak_gb: float | None = None
    swap_growth_gb: float | None = None
    swap_delta_max_gb_per_min: float | None = None
    disk_free_min_gb: float | None = None
    run_size_bytes: int | None = None
    run_file_count: int | None = None


class OperationsSummaryArtifacts(BaseModel):
    """Relative paths joining this summary to its sibling evidence."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    summary: str = "operations-summary.md"
    resource_summary: str = "resource-usage-summary.md"
    process_status: str = ".state/process-status.yaml"


class AgentOperationsSummary(_Explained):
    """All machine-consumed values stored in operations summary frontmatter.

    A section that could not be built is ``null``, and its ``unavailable`` reason names
    the missing evidence or the error raised while reading it.
    """

    run_id: str
    generated_at: datetime
    trigger: Literal["finalization", "status", "command"]
    """``status`` means ``metaproc status`` wrote it while recovering an inactive run."""
    extractor_version: int = OPERATIONS_SUMMARY_EXTRACTOR_VERSION
    extraction_s: float | None = None
    run: RunFigures | None = None
    setup: SetupFigures | None = None
    stages: list[StageRow] | None = None
    items: ItemFigures | None = None
    steps: StepFigures | None = None
    parallelism: ParallelismFigures | None = None
    retries: RetryFigures | None = None
    agents: AgentFigures | None = None
    resources: ResourceFigures | None = None
    artifacts: OperationsSummaryArtifacts = Field(default_factory=OperationsSummaryArtifacts)


class OperationsRollupRow(_Explained):
    """One run's row in ``metaproc operations rollup``.

    A nonzero ``unpriced_invocations`` makes ``list_cost_usd`` and
    ``list_cost_per_item_usd`` lower bounds. ``retries`` counts attempts beyond each
    task's first; ``not_succeeded`` counts every attempt that ended without success.
    """

    run_dir: str
    run_id: str
    state: str | None = None
    items: int | None = None
    elapsed_s: float | None = None
    setup_s: float | None = None
    chain_running_p50_s: float | None = None
    chain_running_p90_s: float | None = None
    chain_running_max_s: float | None = None
    chains_under_target: int | None = None
    chains_within_target: int | None = None
    chains_over_target: int | None = None
    elapsed_per_item_s: float | None = None
    list_cost_usd: float | None = None
    list_cost_per_item_usd: float | None = None
    unpriced_invocations: int = 0
    peak_running: int | None = None
    mean_running: float | None = None
    ceiling: int | None = None
    rss_bytes_max: int | None = None
    swap_used_peak_gb: float | None = None
    retries: int | None = None
    not_succeeded: int | None = None
    summary_source: Literal["written", "computed"]


class OperationsRollup(BaseModel):
    """Several runs side by side against a per-item chain target."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    target_min_s: float
    target_max_s: float
    rows: list[OperationsRollupRow] = Field(default_factory=list)
