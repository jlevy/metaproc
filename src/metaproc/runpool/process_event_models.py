"""Typed Pydantic models for `process-events.jsonl`.

Mirrors `metaproc.runpool.event_models.RunPoolEvent` so the orchestrator
event stream and the worker event stream share the same shape on disk
and the same typed-read discipline in consumers.

The ``hierarchy_*`` fields below let resource reporting map process events
onto the same qualified IDs the process visualization uses.
Today's emit sites in ``runpool/process_events.py`` only know
``step_id``/``process``; qualified IDs are optional during migration so
downstream consumers can fall back to path and plan context when needed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Shared envelope ────────────────────────────────────────────────


class _ProcessEventBase(BaseModel):
    """Common fields on every line of `process-events.jsonl`.

    ``ts`` is always set by the writer (`ProcessEventLogger`). The
    optional ``subgraph_key`` plus qualified ``process_node_id`` /
    ``step_node_id`` exist so the resource-observability joiner can
    pin events to the exact node IDs the visualization plane uses
    without rewalking the log file path.
    """

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    subgraph_key: str | None = None
    process_node_id: str | None = None


# ── Process-level events ───────────────────────────────────────────


class ProcessStartEvent(_ProcessEventBase):
    event: Literal["process_start"] = "process_start"
    process: str
    run_id: str
    backend: str
    step_count: int


class ProcessCompleteEvent(_ProcessEventBase):
    event: Literal["process_complete"] = "process_complete"
    process: str
    run_id: str
    completed: int
    failed: int
    skipped: int
    elapsed_s: float


# ── Level events ───────────────────────────────────────────────────


class LevelStartEvent(_ProcessEventBase):
    event: Literal["level_start"] = "level_start"
    level: int
    steps: list[str]


class LevelCompleteEvent(_ProcessEventBase):
    event: Literal["level_complete"] = "level_complete"
    level: int
    elapsed_s: float


# ── Step events ────────────────────────────────────────────────────


class _StepEventBase(_ProcessEventBase):
    """Step-scoped events all carry the same locating fields."""

    step_id: str
    step_node_id: str | None = None  # qualified viz node ID; optional during migration


class StepStartEvent(_StepEventBase):
    event: Literal["step_start"] = "step_start"
    mode: str


class StepCompleteEvent(_StepEventBase):
    event: Literal["step_complete"] = "step_complete"
    elapsed_s: float


class StepFailEvent(_StepEventBase):
    event: Literal["step_fail"] = "step_fail"
    elapsed_s: float
    error: str = ""


class StepSkipEvent(_StepEventBase):
    event: Literal["step_skip"] = "step_skip"
    reason: str


class StepBlockedEvent(_StepEventBase):
    event: Literal["step_blocked"] = "step_blocked"


class WorkerDispatchEvent(_StepEventBase):
    event: Literal["worker_dispatch"] = "worker_dispatch"
    num_workers: int
    items_count: int


# ── Item lifecycle events ──────────────────────────────────────────


class _ItemEventBase(_StepEventBase):
    """Per-item lifecycle events for fan-out steps."""

    item_key: str
    worker_id: str | None = None


class ItemStartEvent(_ItemEventBase):
    event: Literal["item_start"] = "item_start"


class ItemCompleteEvent(_ItemEventBase):
    event: Literal["item_complete"] = "item_complete"
    elapsed_s: float | None = None


class ItemFailEvent(_ItemEventBase):
    event: Literal["item_fail"] = "item_fail"
    elapsed_s: float | None = None
    error: str = ""
    failure_class: str | None = None


# ── Discriminated union ────────────────────────────────────────────


ProcessEvent = Annotated[
    ProcessStartEvent
    | ProcessCompleteEvent
    | LevelStartEvent
    | LevelCompleteEvent
    | StepStartEvent
    | StepCompleteEvent
    | StepFailEvent
    | StepSkipEvent
    | StepBlockedEvent
    | WorkerDispatchEvent
    | ItemStartEvent
    | ItemCompleteEvent
    | ItemFailEvent,
    Field(discriminator="event"),
]
"""Discriminated union over every event type on `process-events.jsonl`."""
