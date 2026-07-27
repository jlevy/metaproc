"""Pydantic models for run resource observability.

This module covers both data products in one place:

- ``resource-events.jsonl`` — raw, append-only attribution evidence
  (`ResourceEvent` discriminated union over `event`).
- ``resources.json`` — normalised roll-up document
  (`ResourcesDocument` with a recursive `Node` tree, `PrefixRollup`
  taxonomy lists, and `SourceLog` evidence inventory).

Field discipline:

- ``Metrics`` fields are nullable: ``None`` for unavailable evidence,
  ``0`` (or ``0.0``) for measured zero. Callers must respect that
  distinction.
- ``hierarchy`` on raw events is a flat envelope; ancestry lives in the
  recursive ``Node`` tree.
- ``taxonomy_rollups`` on the document is persisted as ordered lists of
  ``PrefixRollup`` entries because JSON keys must be strings (tuple keys
  in `PathTally` are an internal detail).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from metaproc.logutil.tool_failures import FailureKind

# ── Common envelopes ───────────────────────────────────────────────


class Metrics(BaseModel):
    """Canonical normalised metric set.

    Every field is nullable. Use ``None`` when no evidence is available
    and ``0`` (or ``0.0``) when measured zero — they mean different things
    to operators reading the report.
    """

    model_config = ConfigDict(extra="forbid")

    wall_time_s: float | None = None
    active_cpu_s: float | None = None
    cpu_pct_avg: float | None = None
    cpu_pct_max: float | None = None
    rss_bytes_avg: float | None = None
    rss_bytes_max: int | None = None
    billable_vm_hours: float | None = None
    billable_vcpu_hours: float | None = None
    billable_memory_gib_hours: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    actual_cost_usd: float | None = None
    list_cost_usd: float | None = None
    tool_calls: int | None = None
    tool_failures: int | None = None
    wait_throttling_s: float | None = None
    wait_rate_limit_s: float | None = None
    wait_budget_s: float | None = None
    wait_network_s: float | None = None
    tool_exec_s: float | None = None
    local_compute_s: float | None = None


class HierarchyRef(BaseModel):
    """Flat hierarchy assignment for a single event.

    Only ``run_id`` is required. The roll-up builder resolves ancestry
    from the viz `PlanBundle`; raw events carry only the deepest node
    they can be pinned to.

    ``execution_profile`` and ``lane_id`` are populated by writers that
    have access to the runtime env (the external tool ResourceEvent writer
    reads ``EXECUTION_PROFILE`` / ``METAPROC_LANE_ID`` from the env that
    ``run_parallel`` injects). They are optional so older events stay
    parseable and writers without env visibility don't need to invent
    values.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    process_node_id: str | None = None
    step_node_id: str | None = None
    item_key: str | None = None
    worker_id: str | None = None
    file_path: str | None = None
    tool_name: str | None = None
    execution_profile: str | None = None
    lane_id: str | None = None


SourceKind = str


class SourceRef(BaseModel):
    """Pointer to the originating evidence on disk.

    ``path`` is run-root-relative; absolute paths are normalised by the
    writer. ``size_bytes`` and ``mtime_ns`` are captured at extraction
    time so consumers can detect a stale source without re-hashing.
    """

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    path: str | None = None
    line_offset: int | None = None
    size_bytes: int | None = None
    mtime_ns: int | None = None
    span_ids: list[str] = Field(default_factory=list)


class TaxonomyPaths(BaseModel):
    """Multi-family taxonomy path assignment.

    Each value is the structured ``list[str]`` form. The canonical
    ``"a > b > c"`` rendering lives in `metaproc.stats.path_tally`.
    Plugin-owned families go in ``extra_paths`` to keep the closed core
    set validatable.
    """

    model_config = ConfigDict(extra="forbid")

    time_kind_path: list[str] | None = None
    cost_kind_path: list[str] | None = None
    provider_path: list[str] | None = None
    model_path: list[str] | None = None
    tool_path: list[str] | None = None
    resource_path: list[str] | None = None
    policy_path: list[str] | None = None
    extra_paths: dict[str, list[str]] = Field(default_factory=dict)


class _ResourceEventBase(BaseModel):
    """Common envelope shared by every line in `resource-events.jsonl`.

    Subclasses pin ``event`` to a discriminator literal and may make
    optional fields required by redeclaring them.
    """

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    span_id: str | None = None
    parent_span_id: str | None = None
    hierarchy: HierarchyRef
    metrics: Metrics = Field(default_factory=Metrics)
    taxonomy: TaxonomyPaths = Field(default_factory=TaxonomyPaths)
    source: SourceRef


# ── ResourceEvent discriminated union ──────────────────────────────


class SpanStartEvent(_ResourceEventBase):
    event: Literal["span_start"] = "span_start"


class SpanEndEvent(_ResourceEventBase):
    event: Literal["span_end"] = "span_end"


class UsageEvent(_ResourceEventBase):
    """Point-in-time token / cost record from an agent session.

    ``failure_kind`` is populated by the upstream event emitter when the
    provider sub-call failed (e.g. ``rate_limit_exhausted`` for a 429,
    ``tool_rejected`` for auth, ``tool_error`` for other API errors). On
    successful calls it is ``None``.
    """

    event: Literal["usage"] = "usage"
    failure_kind: FailureKind | None = None


class WaitEvent(_ResourceEventBase):
    """Explicit wait bucket (throttling / budget / network)."""

    event: Literal["wait"] = "wait"


class ToolCallEvent(_ResourceEventBase):
    """Tool invocation outcome."""

    event: Literal["tool_call"] = "tool_call"
    failure_kind: FailureKind | None = None


class SampleEvent(_ResourceEventBase):
    """psutil sample for a code step."""

    event: Literal["sample"] = "sample"


class BillingEvent(_ResourceEventBase):
    """GCP Batch billable metric."""

    event: Literal["billing"] = "billing"


class ItemStartEvent(_ResourceEventBase):
    """Per-item lifecycle: started."""

    event: Literal["item_start"] = "item_start"


class ItemCompleteEvent(_ResourceEventBase):
    """Per-item lifecycle: completed."""

    event: Literal["item_complete"] = "item_complete"


class ItemFailEvent(_ResourceEventBase):
    """Per-item lifecycle: failed."""

    event: Literal["item_fail"] = "item_fail"
    error: str = ""
    failure_class: str | None = None


ResourceEvent = Annotated[
    SpanStartEvent
    | SpanEndEvent
    | UsageEvent
    | WaitEvent
    | ToolCallEvent
    | SampleEvent
    | BillingEvent
    | ItemStartEvent
    | ItemCompleteEvent
    | ItemFailEvent,
    Field(discriminator="event"),
]
"""Discriminated union over every event type on `resource-events.jsonl`."""


# ── resources.json roll-up document ────────────────────────────────


NodeType = Literal["run", "process", "step", "item", "file", "tool"]
Attribution = Literal["measured", "estimated", "run_overhead"]


class LogSummary(BaseModel):
    """Lightweight evidence-volume summary for a node or source log."""

    model_config = ConfigDict(extra="forbid")

    source_log_count: int = 0
    event_count: int = 0
    error_count: int = 0
    tool_call_count: int = 0
    tool_failure_count: int = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None


class SourceLog(BaseModel):
    """One log/evidence file consumed by the roll-up builder."""

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    path: str
    adapter: str | None = None
    owner_node_id: str | None = None
    summary: LogSummary = Field(default_factory=LogSummary)


class PrefixRollup(BaseModel):
    """One taxonomy-prefix entry on the persisted roll-up document.

    Persisted as a list of these entries (not a dict keyed by tuple) so
    `resources.json` is JSON-valid.
    """

    model_config = ConfigDict(extra="forbid")

    path: list[str]
    canonical: str
    metrics: Metrics


class Node(BaseModel):
    """One recursive node in the resource hierarchy tree."""

    model_config = ConfigDict(extra="forbid")

    node_type: NodeType
    node_id: str
    label: str
    parent_id: str | None = None
    children: list[Node] = Field(default_factory=list)
    self_metrics: Metrics = Field(default_factory=Metrics)
    total_metrics: Metrics = Field(default_factory=Metrics)
    attribution: Attribution = "measured"
    log_summary: LogSummary = Field(default_factory=LogSummary)
    source_refs: list[SourceRef] = Field(default_factory=list)


SCHEMA_V1 = "metaproc.resources/v1"


class ResourcesDocument(BaseModel):
    """Top-level `resources.json` document."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["metaproc.resources/v1"] = Field(default=SCHEMA_V1, alias="schema")
    run_id: str
    generated_at: datetime
    source_events_path: str
    hierarchy_root: Node
    taxonomy_rollups: dict[str, list[PrefixRollup]] = Field(default_factory=dict)
    source_logs: list[SourceLog] = Field(default_factory=list)
    unattributed: Metrics = Field(default_factory=Metrics)
