"""Runtime state models — .state/ directory records.

These models track execution state: what was launched (AttemptRecord),
current status (StatusRecord), validated results (ResultRecord), and
manual-step acknowledgments (ManualAckRecord).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metaproc.ids import typed_id_pattern
from metaproc.models.authored import StepStatus


class StepState(StrEnum):
    """Per-step state surfaced to operators by ``metaproc status``.

    Single source of truth for the five-state vocabulary:

    - ``current``: completed, outputs valid, fingerprint matches the
      current process definition.
    - ``stale``: completed, outputs valid, but the current step
      fingerprint differs from the recorded one (operator edited the
      runbook since the last completion).
    - ``invalidated``: a prior completion record was renamed ``.stale`` by
      ``--force`` or a fingerprint cascade — the step will rerun.
    - ``missing``: never started, or started and failed without a
      recorded completion.
    - ``in_flight``: actively running.
    """

    current = "current"
    stale = "stale"
    invalidated = "invalidated"
    missing = "missing"
    in_flight = "in_flight"


class OutputFailureKind(StrEnum):
    """What went wrong with a declared output, from facts validation already has.

    This is the only judgement the framework makes about a contract failure.
    It carries no severity and no ownership: whether a ``structural`` failure is
    worth retrying, or whose fault it is, is the domain's to say (see
    ``metaproc-design.md`` §13).

    Subdivides ``FailureClass.INVALID_OUTPUT``, which aggregates all of these
    into one bucket for operator-facing counts.
    """

    missing = "missing"
    """The declared file or directory is not there."""

    empty = "empty"
    """A directory output exists with no content — the silent-success case."""

    unreadable = "unreadable"
    """Present but undecodable: bad frontmatter, invalid UTF-8."""

    structural = "structural"
    """The JSON Schema pass refused it."""

    semantic = "semantic"
    """A model validator refused it — the shape parsed, the rules did not hold."""


class OutputFailure(BaseModel):
    """One declared output that failed validation, with what refused it.

    Carries softschema's stable vocabulary through Metaproc's existing fields:
    ``invariant`` is the structural error code or semantic validator,
    ``location`` is the complete affected path, and ``message`` is the text
    softschema produced.
    """

    output: str
    """The declared output name from the process spec."""

    path: str
    """Rendered artifact path."""

    contract: str | None = None
    """Contract id, when the output declared one."""

    kind: OutputFailureKind
    invariant: str | None = None
    location: str | None = None
    message: str
    """Human text, exactly as it has always appeared after the filename."""

    def summary(self) -> str:
        """Render the one-line form that ``StatusRecord.error`` has always held."""
        return f"{Path(self.path).name}: {self.message}"


class StatusRecord(BaseModel):
    """Per-item harness-owned status — written to .state/status.yaml."""

    run_id: str
    step_id: str
    item: dict[str, str]
    state: StepStatus
    attempt: int = 1
    attempt_id: str | None = Field(default=None, pattern=typed_id_pattern("att"))
    generation: int = Field(default=1, ge=1)
    fence_epoch: int = Field(default=0, ge=0)
    started_at: str = ""
    completed_at: str | None = None
    last_heartbeat_at: str | None = None
    error: str | None = None
    failure_class: str | None = None
    """Operational class of the failure, when there is one.

    A contract failure is placed by ``output_failures``; an operational one has no
    structured record, so without this the durable record can only offer the message.
    Design test 17 asks that every failed attempt name its layer and class, and this is
    the half of that answer the per-item record owes.
    """

    output_failures: list[OutputFailure] = Field(default_factory=list)

    """Structured form of a contract failure, when ``error`` describes one.

    ``error`` stays as written so existing readers keep working; this is what
    lets a reader ask which invariant refused which output without parsing it.
    """


class AttemptRecord(BaseModel):
    """Records what was launched — written to .state/attempt.yaml."""

    run_id: str
    step_id: str
    item: dict[str, str]
    params: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    runtime: dict[str, object] = Field(default_factory=dict)
    step_hash: str | None = None


class AttemptDisposition(StrEnum):
    """Scheduler meaning of one completed execution attempt."""

    succeeded = "succeeded"
    retryable = "retryable"
    permanent = "permanent"
    cancelled = "cancelled"
    lost = "lost"


class TaskAttemptRecord(BaseModel):
    """One durable attempt, retained after later attempts start.

    The record is created before launch and may be rewritten exactly once to add its
    terminal disposition. A terminal record is immutable. The direct ``attempt.yaml``
    beside it remains the legacy latest-launch snapshot; this record is the attempt
    history used by replay and the task scheduler.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["metaproc:TaskAttemptRecord/0.1"] = Field(
        default="metaproc:TaskAttemptRecord/0.1", alias="schema"
    )
    attempt_id: str = Field(pattern=typed_id_pattern("att"))
    run_id: str
    step_id: str
    item_key: str | None = None
    item: dict[str, str] = Field(default_factory=dict)
    scope_path: list[str] = Field(default_factory=list)
    generation: int = Field(default=1, ge=1)
    fence_epoch: int = Field(default=0, ge=0)
    attempt_number: int = Field(ge=1)
    started_at: str
    disposition: AttemptDisposition | None = None
    ended_at: str | None = None
    failure_class: str | None = None
    error: str | None = None
    output_failures: list[OutputFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def _terminal_fields_move_together(self) -> Self:
        if (self.disposition is None) != (self.ended_at is None):
            raise ValueError("disposition and ended_at must either both be set or both be absent")
        has_failure = bool(self.failure_class or self.error or self.output_failures)
        if self.disposition is None and has_failure:
            raise ValueError("live attempt cannot carry terminal failure fields")
        if self.disposition is AttemptDisposition.succeeded and has_failure:
            raise ValueError("succeeded attempt cannot carry terminal failure fields")
        return self


class ResultRecord(BaseModel):
    """Validated outcome — written to .state/result.yaml after output check."""

    run_id: str
    step_id: str
    attempt_id: str | None = Field(default=None, pattern=typed_id_pattern("att"))
    state: StepStatus
    validated: bool
    outputs: dict[str, str] = Field(default_factory=dict)
    published_at: str = ""
    step_hash: str | None = None


class ManualAckRecord(BaseModel):
    """Operator acknowledgment for a manual step — written to .state/manual-ack.yaml."""

    run_id: str
    step_id: str
    operator: str
    acknowledged_at: str
    note: str | None = None


# ── Generic map-reduce item models ────────────────────────────────


class MapItem(BaseModel):
    """One item in a fan-out source file.

    Has no hardcoded fields — all fields accepted via extra="allow".
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")


class MapItemsFrontmatter(BaseModel):
    """Generic frontmatter for a fan-out source file."""

    items: list[MapItem] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")


# ── Envelope wrappers ─────────────────────────────────────────

# ProcessEnvelope lives in metaproc.io.frontmatter to avoid circular imports.


# ── Terminal status registry ─────────────────────────────────────

_TERMINAL_STATUSES: set[str] = {"completed", "cached"}


def register_terminal_statuses(statuses: frozenset[str]) -> None:
    """Register additional terminal statuses for local/manual callers."""
    _TERMINAL_STATUSES.update(statuses)


def get_terminal_statuses() -> frozenset[str]:
    """Return the merged set of terminal statuses from all domains."""
    statuses = set(_TERMINAL_STATUSES)
    try:
        from metaproc.plugins.discovery import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            get_plugin_registry,
        )
    except ImportError:
        return frozenset(statuses)
    statuses.update(get_plugin_registry().terminal_statuses)
    return frozenset(statuses)
