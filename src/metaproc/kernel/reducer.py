"""The scheduler core as a pure function.

``reduce(state, event, now) -> (state, commands)``.

No subprocesses, no filesystem, no clock. Time arrives as a parameter so retry backoff
and deadlines are testable without waiting for them. Events are durable facts arriving;
commands are what the runtime should do next.

Command emission is derived from the resulting state rather than accumulated along the
way. That is what makes replay deterministic: feeding the same facts in any order that
respects causality lands on the same state and proposes the same next actions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from metaproc.kernel.model import (
    AttemptDisposition,
    AttemptRecord,
    Cardinality,
    ClauseMapping,
    CommitRecord,
    DependencyClause,
    ExpansionRecord,
    ExpansionState,
    KernelState,
    Outcome,
    Requirement,
    TaskKey,
    TaskRecord,
    TaskState,
)

ClauseStatus = Literal["satisfied", "pending", "dead"]

RETRY_BASE_SECONDS = 10.0
RETRY_MAX_SECONDS = 600.0
MAX_ATTEMPTS = 3


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class ExpansionClosed:
    """A roster generation is complete and no further items can appear."""

    step_id: str
    keys: tuple[str, ...]
    generation: int = 1
    key_space: str | None = None
    derived_from: str | None = None
    producer_commit: str | None = None


@dataclass(frozen=True)
class ExpansionFailed:
    step_id: str
    reason: str
    generation: int = 1


@dataclass(frozen=True)
class AttemptStarted:
    attempt_id: str
    task_key: TaskKey
    generation: int
    fence_epoch: int


@dataclass(frozen=True)
class AttemptEnded:
    attempt_id: str
    disposition: AttemptDisposition
    manifest: tuple[str, ...] = ()
    failure_category: str | None = None


@dataclass(frozen=True)
class ForceIssued:
    """Operator invalidation of one task, cascading along dependency mappings."""

    task_key: TaskKey


@dataclass(frozen=True)
class Tick:
    """Time passed; wake retry-waiting work."""


Event = ExpansionClosed | ExpansionFailed | AttemptStarted | AttemptEnded | ForceIssued | Tick


# ------------------------------------------------------------------------- commands


@dataclass(frozen=True)
class MaterializeExpansion:
    step_id: str


@dataclass(frozen=True)
class DispatchAttempt:
    task_key: TaskKey
    generation: int
    fence_epoch: int


@dataclass(frozen=True)
class ScheduleRetry:
    task_key: TaskKey
    at: float


@dataclass(frozen=True)
class CancelAttempt:
    attempt_id: str
    reason: str


CommandType = MaterializeExpansion | DispatchAttempt | ScheduleRetry | CancelAttempt


# ------------------------------------------------------------------- clause evaluation


def related_keys(
    state: KernelState, downstream: TaskKey, clause: DependencyClause
) -> tuple[TaskKey, ...] | None:
    """Upstream tasks this clause relates to, or None if the roster is not closed yet.

    None is not "empty". An unclosed expansion means the answer is unknowable, and a
    fan-in that treats it as empty fires against an incomplete universe.
    """
    upstream = state.template(clause.upstream_step)
    if upstream is None:
        return ()
    if upstream.expands_over is None:
        return (TaskKey(step_id=upstream.step_id, scope_path=downstream.scope_path),)

    expansion = state.expansion_for(upstream.step_id)
    if expansion is None or not expansion.is_closed:
        return None

    if clause.mapping is ClauseMapping.SAME_KEY:
        if downstream.item_key is None:
            return None
        if downstream.item_key not in expansion.keys:
            return ()
        return (
            TaskKey(
                step_id=upstream.step_id,
                item_key=downstream.item_key,
                scope_path=downstream.scope_path,
            ),
        )

    if clause.mapping is ClauseMapping.BROADCAST:
        return tuple(
            TaskKey(step_id=upstream.step_id, item_key=k, scope_path=downstream.scope_path)
            for k in expansion.keys[:1]
        )

    return tuple(
        TaskKey(step_id=upstream.step_id, item_key=k, scope_path=downstream.scope_path)
        for k in expansion.keys
    )


def _satisfies(record: TaskRecord | None, requirement: Requirement) -> bool:
    if record is None or not record.is_terminal:
        return False
    if requirement is Requirement.SUCCEEDED:
        return record.outcome is Outcome.SUCCEEDED
    return True


def _permanently_unsatisfiable(record: TaskRecord | None, requirement: Requirement) -> bool:
    """True when this upstream can never satisfy the requirement."""
    if record is None or not record.is_terminal:
        return False
    if requirement is Requirement.SUCCEEDED:
        return record.outcome is not Outcome.SUCCEEDED
    return False


def clause_status(
    state: KernelState, downstream: TaskKey, clause: DependencyClause
) -> ClauseStatus:
    """Whether a clause is satisfied, still pending, or dead."""
    related = related_keys(state, downstream, clause)
    if related is None:
        return "pending"

    if not related:
        # Vacuous success for ALL, failure for ANY, per the RFC's empty-roster rule.
        return "satisfied" if clause.cardinality is Cardinality.ALL else "dead"

    if clause.mapping is ClauseMapping.BROADCAST:
        upstream = state.template(clause.upstream_step)
        expansion = (
            state.expansion_for(clause.upstream_step)
            if upstream is not None and upstream.expands_over is not None
            else None
        )
        if expansion is not None and len(expansion.keys) != 1:
            # There is no principled "the one" item of a fan-out. Silently picking the
            # first key would define arbitrary semantics; a deterministic dead clause
            # surfaces the authoring error instead.
            return "dead"

    records = [state.task(k) for k in related]
    if clause.cardinality is Cardinality.ALL:
        if any(_permanently_unsatisfiable(r, clause.requirement) for r in records):
            return "dead"
        return "satisfied" if all(_satisfies(r, clause.requirement) for r in records) else "pending"

    if any(_satisfies(r, clause.requirement) for r in records):
        return "satisfied"
    if all(_permanently_unsatisfiable(r, clause.requirement) for r in records):
        return "dead"
    return "pending"


# ----------------------------------------------------------------- task materialization


def materialized_keys(state: KernelState) -> tuple[TaskKey, ...]:
    """Task keys that currently exist, in a deterministic order.

    Callers testing membership should use ``state.materialized`` instead; this exists
    for the cases that genuinely need an ordering.
    """
    return tuple(sorted(state.materialized))


def task_state(state: KernelState, key: TaskKey) -> TaskState:
    """Project one task's scheduler state from durable facts."""
    template = state.template(key.step_id)
    if template is None:
        return TaskState.UNMATERIALIZED
    if key not in state.materialized:
        return TaskState.UNMATERIALIZED

    record = state.task(key) or TaskRecord(task_key=key)

    outcome = record.outcome
    if outcome is not None and record.outcome_generation == record.desired_generation:
        return {
            Outcome.SUCCEEDED: TaskState.SUCCEEDED,
            Outcome.FAILED: TaskState.FAILED,
            Outcome.CANCELLED: TaskState.CANCELLED,
            Outcome.SKIPPED: TaskState.SKIPPED,
        }[outcome]

    if state.commit_for(key, record.desired_generation) is not None:
        return TaskState.SUCCEEDED

    live = state.live_attempt(key)
    if live is not None:
        return TaskState.RUNNING

    statuses = [clause_status(state, key, c) for c in template.clauses]
    if any(s == "dead" for s in statuses):
        return TaskState.BLOCKED
    if any(s == "pending" for s in statuses):
        return TaskState.WAITING_DEPENDENCIES

    if record.retry_at is not None and record.retry_at > state.now:
        return TaskState.RETRY_WAIT

    return TaskState.READY


def _attempt_count(state: KernelState, key: TaskKey, generation: int) -> int:
    return sum(1 for a in state.attempts if a.task_key == key and a.generation == generation)


def retry_delay(attempt_number: int) -> float:
    """Deterministic exponential backoff, capped. No jitter: replay must be exact."""
    delay = RETRY_BASE_SECONDS * (2 ** max(0, attempt_number - 1))
    return min(delay, RETRY_MAX_SECONDS)


# ------------------------------------------------------------------------- the reducer


def reduce(
    state: KernelState, event: Event, now: float | None = None
) -> tuple[KernelState, tuple[CommandType, ...]]:
    """Apply one durable fact and propose what the runtime should do next."""
    state = replace(state, now=state.now if now is None else now)

    if isinstance(event, ExpansionClosed):
        state = _apply_expansion_closed(state, event)
    elif isinstance(event, ExpansionFailed):
        state = state.with_expansion(
            ExpansionRecord(
                expansion_id=f"{event.step_id}#{event.generation}",
                step_id=event.step_id,
                generation=event.generation,
                state=ExpansionState.FAILED,
                failure_reason=event.reason,
            )
        )
    elif isinstance(event, AttemptStarted):
        state = _apply_attempt_started(state, event)
    elif isinstance(event, AttemptEnded):
        state = _apply_attempt_ended(state, event)
    elif isinstance(event, ForceIssued):
        state = _apply_force(state, event)

    state = _settle_blocked(state)
    return state, _commands(state)


def _apply_expansion_closed(state: KernelState, event: ExpansionClosed) -> KernelState:
    if len(set(event.keys)) != len(event.keys):
        duplicates = sorted({k for k in event.keys if event.keys.count(k) > 1})
        return state.with_expansion(
            ExpansionRecord(
                expansion_id=f"{event.step_id}#{event.generation}",
                step_id=event.step_id,
                generation=event.generation,
                state=ExpansionState.FAILED,
                failure_reason=f"duplicate keys in roster: {', '.join(duplicates)}",
            )
        )
    return state.with_expansion(
        ExpansionRecord(
            expansion_id=f"{event.step_id}#{event.generation}",
            step_id=event.step_id,
            generation=event.generation,
            state=ExpansionState.CLOSED,
            keys=event.keys,
            key_space=event.key_space,
            derived_from=event.derived_from,
            producer_commit=event.producer_commit,
        )
    )


def _apply_attempt_started(state: KernelState, event: AttemptStarted) -> KernelState:
    record = state.task(event.task_key) or TaskRecord(task_key=event.task_key)
    state = state.with_task(replace(record, retry_at=None))
    return state.with_attempt(
        AttemptRecord(
            attempt_id=event.attempt_id,
            task_key=event.task_key,
            generation=event.generation,
            fence_epoch=event.fence_epoch,
            started_at=state.now,
        )
    )


def _apply_attempt_ended(state: KernelState, event: AttemptEnded) -> KernelState:
    attempt = state.attempt(event.attempt_id)
    if attempt is None or attempt.is_terminal:
        return state

    attempt = replace(
        attempt,
        disposition=event.disposition,
        ended_at=state.now,
        failure_category=event.failure_category,
    )
    state = state.with_attempt(attempt)

    record = state.task(attempt.task_key) or TaskRecord(task_key=attempt.task_key)

    if attempt.fence_epoch != record.fence_epoch or attempt.generation != record.desired_generation:
        # The attempt is superseded. Its disposition is recorded above, because history
        # is append-only, and that is ALL it gets: no commit, no outcome, no retry
        # scheduling. Fencing that only guards the commit path lets a stale RETRYABLE
        # put the forced new generation into retry_wait, and a stale PERMANENT clobber
        # the record of a generation that already succeeded.
        return state

    if event.disposition is AttemptDisposition.SUCCEEDED:
        if state.commit_for(attempt.task_key, attempt.generation) is not None:
            # A duplicate completion (redelivery) does not get to publish twice.
            return state
        state = state.with_commit(
            CommitRecord(
                task_key=attempt.task_key,
                generation=attempt.generation,
                attempt_id=attempt.attempt_id,
                manifest=event.manifest,
            )
        )
        return state.with_task(
            replace(
                record,
                outcome=Outcome.SUCCEEDED,
                outcome_generation=attempt.generation,
                retry_at=None,
            )
        )

    if event.disposition is AttemptDisposition.CANCELLED:
        return state.with_task(
            replace(record, outcome=Outcome.CANCELLED, outcome_generation=attempt.generation)
        )

    if event.disposition is AttemptDisposition.PERMANENT:
        return state.with_task(
            replace(record, outcome=Outcome.FAILED, outcome_generation=attempt.generation)
        )

    # RETRYABLE or LOST
    tries = _attempt_count(state, attempt.task_key, attempt.generation)
    if tries >= MAX_ATTEMPTS:
        return state.with_task(
            replace(record, outcome=Outcome.FAILED, outcome_generation=attempt.generation)
        )
    return state.with_task(replace(record, retry_at=state.now + retry_delay(tries)))


def _descendant_steps(state: KernelState, step_id: str) -> tuple[str, ...]:
    """Steps reachable downstream, for force cascade."""
    found: set[str] = set()
    frontier = [step_id]
    while frontier:
        current = frontier.pop()
        for template in state.templates:
            if template.step_id in found:
                continue
            if any(c.upstream_step == current for c in template.clauses):
                found.add(template.step_id)
                frontier.append(template.step_id)
    return tuple(sorted(found))


def _apply_force(state: KernelState, event: ForceIssued) -> KernelState:
    """Invalidate the forced task and everything the dependency mappings reach.

    The cascade is a breadth-first walk, treating each invalidated task as forced for
    the purposes of its own dependents, with same-key narrowing applied per hop. A walk
    over direct clauses only leaves grandchildren holding commits computed over a
    superseded world: force measure[a], and a barrier that consumed interpret[a] keeps
    reporting success while its input is being recomputed.
    """
    frontier: list[TaskKey] = [event.task_key]
    seen: set[TaskKey] = set()
    while frontier:
        key = frontier.pop(0)
        if key in seen:
            continue
        seen.add(key)
        record = state.task(key) or TaskRecord(task_key=key)
        state = state.with_task(
            replace(
                record,
                desired_generation=record.desired_generation + 1,
                fence_epoch=record.fence_epoch + 1,
                outcome=None,
                outcome_generation=None,
                retry_at=None,
            )
        )
        for template in state.templates:
            for clause in template.clauses:
                if clause.upstream_step != key.step_id:
                    continue
                for dkey in state.materialized:
                    if dkey.step_id != template.step_id or dkey in seen:
                        continue
                    if clause.mapping is ClauseMapping.SAME_KEY and dkey.item_key != key.item_key:
                        continue
                    # A dependent with no record never consumed anything, and neither
                    # did anything downstream of it; there is nothing to invalidate.
                    if state.task(dkey) is None:
                        continue
                    frontier.append(dkey)
    return state


def _settle_blocked(state: KernelState) -> KernelState:
    """Give tasks whose clauses died a terminal skipped outcome."""
    for key in materialized_keys(state):
        if task_state(state, key) is not TaskState.BLOCKED:
            continue
        record = state.task(key) or TaskRecord(task_key=key)
        template = state.template(key.step_id)
        dead = (
            [c.upstream_step for c in template.clauses if clause_status(state, key, c) == "dead"]
            if template
            else []
        )
        state = state.with_task(
            replace(
                record,
                outcome=Outcome.SKIPPED,
                outcome_generation=record.desired_generation,
                skip_reason=f"upstream unsatisfiable: {', '.join(sorted(dead))}",
            )
        )
    return state


def _commands(state: KernelState) -> tuple[CommandType, ...]:
    commands: list[CommandType] = []

    for template in state.templates:
        if template.expands_over is None:
            continue
        expansion = state.expansion_for(template.step_id)
        if expansion is None:
            commands.append(MaterializeExpansion(step_id=template.step_id))

    for key in materialized_keys(state):
        record = state.task(key) or TaskRecord(task_key=key)
        current = task_state(state, key)
        if current is TaskState.READY:
            commands.append(
                DispatchAttempt(
                    task_key=key,
                    generation=record.desired_generation,
                    fence_epoch=record.fence_epoch,
                )
            )
        elif current is TaskState.RETRY_WAIT and record.retry_at is not None:
            commands.append(ScheduleRetry(task_key=key, at=record.retry_at))

    for attempt in state.attempts:
        if attempt.is_terminal:
            continue
        record = state.task(attempt.task_key)
        if record is not None and attempt.fence_epoch != record.fence_epoch:
            commands.append(
                CancelAttempt(attempt_id=attempt.attempt_id, reason="superseded generation")
            )

    return tuple(commands)
