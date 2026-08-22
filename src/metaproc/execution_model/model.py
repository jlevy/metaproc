"""Durable facts of a run, and the derived state a scheduler reads.

Frozen dataclasses rather than Pydantic models: this layer is a reference semantics, and
immutability plus structural equality are what make replay determinism testable. Nothing
here validates external input, so Pydantic would buy nothing and would hide the purity.

The vocabulary follows ``docs/execution-model-design.md``. The distinction that drives every
type below is that generations, attempts, commits, expansions, and cancellations are
*facts*, while readiness, blocking, and aggregate status are *projections* of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import cached_property


class Outcome(StrEnum):
    """Terminal outcome of one task generation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class AttemptDisposition(StrEnum):
    """How one attempt ended, which decides what the engine does next.

    Kept separate from the causal category of a failure (rate limit, timeout, invalid
    output). One says what to do, the other says what happened, and collapsing them into
    a single enum is what forces every consumer to re-derive the other axis.
    """

    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    CANCELLED = "cancelled"
    LOST = "lost"


class ClauseMapping(StrEnum):
    """Which upstream tasks relate to a downstream task."""

    SAME_KEY = "same_key"
    BROADCAST = "broadcast"
    COLLECT_ALL = "collect_all"


class Requirement(StrEnum):
    """Which upstream outcomes satisfy a clause."""

    SUCCEEDED = "succeeded"
    FINISHED = "finished"


class Cardinality(StrEnum):
    """How many related upstream tasks are needed."""

    ALL = "all"
    ANY = "any"


class ExpansionState(StrEnum):
    """Lifecycle of one roster generation."""

    UNMATERIALIZED = "unmaterialized"
    MATERIALIZING = "materializing"
    CLOSED = "closed"
    FAILED = "failed"


class TaskState(StrEnum):
    """Projected scheduler state. Never stored; always derived from durable facts."""

    UNMATERIALIZED = "unmaterialized"
    WAITING_DEPENDENCIES = "waiting_dependencies"
    BLOCKED = "blocked"
    READY = "ready"
    ADMISSION_WAIT = "admission_wait"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True, order=True)
class TaskKey:
    """Address of one task.

    ``scope_path`` is the chain of enclosing composite scopes, empty at top level. It is
    what allows a composite to be a scope containing tasks rather than a task that holds
    a resource slot while running a nested scheduler.
    """

    step_id: str
    item_key: str | None = None
    scope_path: tuple[str, ...] = ()

    def __str__(self) -> str:
        scope = "".join(f"{s}/" for s in self.scope_path)
        item = f"[{self.item_key}]" if self.item_key is not None else ""
        return f"{scope}{self.step_id}{item}"


@dataclass(frozen=True)
class DependencyClause:
    """One edge, stated on all four independent axes.

    Collapsing any axis into another works until one consumer has heterogeneous inputs,
    which is exactly when it becomes a migration rather than a refactor.
    """

    upstream_step: str
    mapping: ClauseMapping
    requirement: Requirement
    cardinality: Cardinality = Cardinality.ALL
    key_space: str | None = None


@dataclass(frozen=True)
class RetryPolicy:
    """How many tries a task gets and how long it waits between them.

    Policy is data because the engine's policy is authored per step. Constants in the
    reducer would mean the reference model could not replay any real spec containing a
    transient failure, which is exactly what the equivalence suite needs to do.
    """

    max_attempts: int = 3
    base_seconds: float = 10.0
    max_seconds: float = 600.0
    multiplier: float = 2.0

    def delay_for(self, attempt_number: int) -> float:
        """Deterministic backoff. No jitter: replay must be exact."""
        delay = self.base_seconds * (self.multiplier ** max(0, attempt_number - 1))
        return min(delay, self.max_seconds)


DEFAULT_RETRY_POLICY = RetryPolicy()


@dataclass(frozen=True)
class StepTemplate:
    """Static shape of one step. Width comes from an expansion, not from here."""

    step_id: str
    clauses: tuple[DependencyClause, ...] = ()
    expands_over: str | None = None
    key_space: str | None = None
    retry: RetryPolicy = DEFAULT_RETRY_POLICY


@dataclass(frozen=True)
class ExpansionRecord:
    """Durable materialization of one roster generation.

    ``keys`` is meaningful only once ``state`` is CLOSED. A fan-in that fires over an
    open expansion is the premature-barrier bug this record exists to prevent.
    """

    expansion_id: str
    step_id: str
    generation: int
    state: ExpansionState
    keys: tuple[str, ...] = ()
    key_space: str | None = None
    derived_from: str | None = None
    producer_commit: str | None = None
    failure_reason: str | None = None

    @property
    def is_closed(self) -> bool:
        return self.state is ExpansionState.CLOSED

    @cached_property
    def key_set(self) -> frozenset[str]:
        """Membership index for the roster.

        Clause evaluation asks "is this item in the roster" once per aligned edge per
        scheduling pass, so a tuple scan here is an O(width) term inside an O(tasks)
        loop, which is the quadratic the envelope guard exists to catch.
        """
        return frozenset(self.keys)


@dataclass(frozen=True)
class AttemptRecord:
    """One execution try. Append-only; never rewritten once terminal."""

    attempt_id: str
    task_key: TaskKey
    generation: int
    fence_epoch: int
    started_at: float
    disposition: AttemptDisposition | None = None
    ended_at: float | None = None
    failure_category: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.disposition is not None


@dataclass(frozen=True)
class CommitRecord:
    """The single durable fact that a task generation published validated outputs."""

    task_key: TaskKey
    generation: int
    attempt_id: str
    manifest: tuple[str, ...] = ()
    upstream_commits: tuple[str, ...] = ()

    @property
    def commit_id(self) -> str:
        return f"{self.task_key}@{self.generation}"


@dataclass(frozen=True)
class TaskRecord:
    """Current desired generation and terminal outcome for one task.

    A retry does not change ``desired_generation``; a force does. That split is what
    makes invalidation precise instead of directory-shaped.
    """

    task_key: TaskKey
    desired_generation: int = 1
    outcome: Outcome | None = None
    outcome_generation: int | None = None
    retry_at: float | None = None
    fence_epoch: int = 0
    skip_reason: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.outcome is not None and self.outcome_generation == self.desired_generation


@dataclass(frozen=True)
class RunState:
    """Everything durable about a run, plus the templates it executes.

    Held as sorted tuples rather than dicts so two states built by different event orders
    compare equal when they represent the same facts, which is what invariant 5 (replay
    determinism) actually asserts.
    """

    templates: tuple[StepTemplate, ...] = ()
    expansions: tuple[ExpansionRecord, ...] = ()
    tasks: tuple[TaskRecord, ...] = ()
    attempts: tuple[AttemptRecord, ...] = ()
    commits: tuple[CommitRecord, ...] = ()

    # Observation time, not a durable fact: two states holding the same facts are the
    # same state whether or not the clock has moved, which is what lets the replay
    # tests compare whole states.
    now: float = field(default=0.0, compare=False)

    # -- lookups ---------------------------------------------------------------
    #
    # Indexed rather than scanned. Linear scans here are not a micro-optimisation
    # question: the scheduler asks these per task per event, so an O(n) lookup makes a
    # scheduling pass quadratic in roster width and the run unusable at full width.
    # Measured before indexing, one pass over a 600-task graph took 0.25s and grew 4x
    # per doubling.
    #
    # ``cached_property`` writes straight into ``__dict__``, so it works on a frozen
    # dataclass and stays out of field-based equality: two states built from the same
    # facts still compare equal whether or not either has been queried.

    @cached_property
    def _templates_by_id(self) -> dict[str, StepTemplate]:
        return {t.step_id: t for t in self.templates}

    @cached_property
    def _latest_expansion(self) -> dict[str, ExpansionRecord]:
        latest: dict[str, ExpansionRecord] = {}
        for e in self.expansions:
            known = latest.get(e.step_id)
            if known is None or e.generation > known.generation:
                latest[e.step_id] = e
        return latest

    @cached_property
    def _tasks_by_key(self) -> dict[TaskKey, TaskRecord]:
        return {t.task_key: t for t in self.tasks}

    @cached_property
    def _commits_by_generation(self) -> dict[tuple[TaskKey, int], CommitRecord]:
        return {(c.task_key, c.generation): c for c in self.commits}

    @cached_property
    def _attempts_by_id(self) -> dict[str, AttemptRecord]:
        return {a.attempt_id: a for a in self.attempts}

    @cached_property
    def _live_attempt_by_key(self) -> dict[TaskKey, AttemptRecord]:
        return {a.task_key: a for a in self.attempts if not a.is_terminal}

    def template(self, step_id: str) -> StepTemplate | None:
        return self._templates_by_id.get(step_id)

    def expansion_for(self, step_id: str) -> ExpansionRecord | None:
        """Latest expansion generation for a step."""
        return self._latest_expansion.get(step_id)

    def task(self, key: TaskKey) -> TaskRecord | None:
        return self._tasks_by_key.get(key)

    def commit_for(self, key: TaskKey, generation: int) -> CommitRecord | None:
        return self._commits_by_generation.get((key, generation))

    def attempt(self, attempt_id: str) -> AttemptRecord | None:
        return self._attempts_by_id.get(attempt_id)

    def live_attempt(self, key: TaskKey) -> AttemptRecord | None:
        """The one non-terminal attempt for a task, if any."""
        return self._live_attempt_by_key.get(key)

    @cached_property
    def materialized(self) -> frozenset[TaskKey]:
        """Every task key that currently exists, given each closed expansion."""
        keys: set[TaskKey] = set()
        for template in self.templates:
            if template.expands_over is None:
                keys.add(TaskKey(step_id=template.step_id))
                continue
            expansion = self._latest_expansion.get(template.step_id)
            if expansion is not None and expansion.is_closed:
                keys.update(TaskKey(step_id=template.step_id, item_key=k) for k in expansion.keys)
        return frozenset(keys)

    # -- functional update -----------------------------------------------------

    def with_task(self, record: TaskRecord) -> RunState:
        others = tuple(t for t in self.tasks if t.task_key != record.task_key)
        return replace(self, tasks=_sorted_tasks(others + (record,)))

    def with_attempt(self, record: AttemptRecord) -> RunState:
        others = tuple(a for a in self.attempts if a.attempt_id != record.attempt_id)
        return replace(self, attempts=_sorted_attempts(others + (record,)))

    def with_commit(self, record: CommitRecord) -> RunState:
        return replace(self, commits=_sorted_commits(self.commits + (record,)))

    def with_expansion(self, record: ExpansionRecord) -> RunState:
        others = tuple(
            e
            for e in self.expansions
            if not (e.step_id == record.step_id and e.generation == record.generation)
        )
        return replace(self, expansions=_sorted_expansions(others + (record,)))


def _sorted_tasks(items: tuple[TaskRecord, ...]) -> tuple[TaskRecord, ...]:
    return tuple(
        sorted(
            items,
            key=lambda t: (t.task_key.scope_path, t.task_key.step_id, t.task_key.item_key or ""),
        )
    )


def _sorted_attempts(items: tuple[AttemptRecord, ...]) -> tuple[AttemptRecord, ...]:
    return tuple(sorted(items, key=lambda a: a.attempt_id))


def _sorted_commits(items: tuple[CommitRecord, ...]) -> tuple[CommitRecord, ...]:
    return tuple(sorted(items, key=lambda c: (str(c.task_key), c.generation)))


def _sorted_expansions(items: tuple[ExpansionRecord, ...]) -> tuple[ExpansionRecord, ...]:
    return tuple(sorted(items, key=lambda e: (e.step_id, e.generation)))
