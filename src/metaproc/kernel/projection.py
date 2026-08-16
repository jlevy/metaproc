"""The typed status projection, derived from durable facts.

A status file is a *view*. It is rebuildable from generations, attempts, commits,
expansions, and reservations, it is versioned so a reader can tell what it is looking at,
and nothing schedules from it. Treating a mutable status document as truth is how a run
ends up with two disagreeing answers about whether a task finished.

The part worth designing carefully is the blocker. For every task that is not running,
one primary reason, in the vocabulary below, with the specific upstream tasks or roster
named. "Pending" tells an operator nothing at three in the morning; "waiting on
`measure[ACME]`, which failed" ends the investigation.

Ordering is deliberate. A task can be blocked several ways at once, and reporting the
most actionable one matters more than reporting all of them: an unsatisfiable dependency
outranks an unclosed roster, which outranks a retry timer, because that is the order in
which an operator can do something about them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from metaproc.kernel.model import KernelState, TaskKey, TaskState
from metaproc.kernel.reducer import clause_status, related_keys, task_state

PROJECTION_CONTRACT = "metaproc:ProcessStatus/0.1"


class BlockerKind(StrEnum):
    """Why a task is not running. One primary reason per task."""

    DEPENDENCY_UNSATISFIABLE = "dependency_unsatisfiable"
    DEPENDENCY_PENDING = "dependency_pending"
    EXPANSION_NOT_CLOSED = "expansion_not_closed"
    SCOPE_NOT_INSTANTIATED = "scope_not_instantiated"
    RETRY_BACKOFF = "retry_backoff"
    ADMISSION_WAIT = "admission_wait"
    BUDGET_WAIT = "budget_wait"
    WAITING_EXTERNAL = "waiting_external"
    UPSTREAM_GENERATION_CHANGED = "upstream_generation_changed"


@dataclass(frozen=True)
class Blocker:
    """One primary reason, plus the specific facts behind it."""

    kind: BlockerKind
    detail: str
    upstream: tuple[str, ...] = ()
    until: float | None = None


@dataclass(frozen=True)
class TaskView:
    key: str
    step_id: str
    item_key: str | None
    state: TaskState
    generation: int
    attempts: int
    blocker: Blocker | None = None


@dataclass(frozen=True)
class StepView:
    """Aggregate over one step. `partial` lives here, never on a task."""

    step_id: str
    total: int
    succeeded: int
    failed: int
    cancelled: int
    skipped: int
    running: int
    ready: int
    waiting: int

    @property
    def outcome(self) -> str:
        if self.total == 0:
            return "succeeded"
        terminal = self.succeeded + self.failed + self.cancelled + self.skipped
        if terminal < self.total:
            return "incomplete"
        if self.succeeded == self.total:
            return "succeeded"
        if self.succeeded == 0:
            return "failed"
        return "partial"

    @property
    def coverage(self) -> float:
        """Share of the step that produced a commit. Partial success is a normal
        product state, so coverage is a first-class number rather than a footnote."""
        return 1.0 if self.total == 0 else self.succeeded / self.total


@dataclass(frozen=True)
class ProcessStatus:
    """The whole projection. Versioned, rebuildable, never authoritative."""

    schema_version: str = PROJECTION_CONTRACT
    snapshot_generation: int = 0
    tasks: tuple[TaskView, ...] = ()
    steps: tuple[StepView, ...] = ()

    @property
    def outcome(self) -> str:
        if not self.steps:
            return "succeeded"
        outcomes = {s.outcome for s in self.steps}
        if outcomes == {"succeeded"}:
            return "succeeded"
        if "incomplete" in outcomes:
            return "incomplete"
        if outcomes == {"failed"}:
            return "failed"
        return "partial"

    def blocked_by(self, kind: BlockerKind) -> tuple[TaskView, ...]:
        return tuple(t for t in self.tasks if t.blocker is not None and t.blocker.kind is kind)


def blocker_for(state: KernelState, key: TaskKey) -> Blocker | None:
    """The one primary reason this task is not running, or None if it is not blocked."""
    current = task_state(state, key)
    if current in {
        TaskState.RUNNING,
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.SKIPPED,
    }:
        return None

    template = state.template(key.step_id)
    if current is TaskState.UNMATERIALIZED:
        if template is not None and template.expands_over is not None:
            expansion = state.expansion_for(key.step_id)
            detail = (
                f"roster for '{key.step_id}' has not been produced yet"
                if expansion is None
                else f"roster generation {expansion.generation} for '{key.step_id}' is {expansion.state}"
            )
            return Blocker(kind=BlockerKind.EXPANSION_NOT_CLOSED, detail=detail)
        return Blocker(
            kind=BlockerKind.SCOPE_NOT_INSTANTIATED,
            detail=f"no scope instance for '{key.step_id}'",
        )

    if template is not None:
        # Unsatisfiable first: it is terminal, and no amount of waiting helps.
        for clause in template.clauses:
            if clause_status(state, key, clause) == "dead":
                related = related_keys(state, key, clause) or ()
                names = tuple(
                    str(r)
                    for r in related
                    if (rec := state.task(r)) is not None and rec.is_terminal
                )
                return Blocker(
                    kind=BlockerKind.DEPENDENCY_UNSATISFIABLE,
                    detail=(
                        f"'{clause.upstream_step}' can no longer satisfy "
                        f"require={clause.requirement}"
                    ),
                    upstream=names,
                )

        for clause in template.clauses:
            if clause_status(state, key, clause) != "pending":
                continue
            related = related_keys(state, key, clause)
            if related is None:
                expansion = state.expansion_for(clause.upstream_step)
                generation = "unproduced" if expansion is None else str(expansion.generation)
                return Blocker(
                    kind=BlockerKind.EXPANSION_NOT_CLOSED,
                    detail=(
                        f"waiting for roster '{clause.upstream_step}' generation "
                        f"{generation} to close"
                    ),
                )
            outstanding = tuple(
                str(r) for r in related if (rec := state.task(r)) is None or not rec.is_terminal
            )
            return Blocker(
                kind=BlockerKind.DEPENDENCY_PENDING,
                detail=f"waiting on {len(outstanding)} upstream task(s) in '{clause.upstream_step}'",
                upstream=outstanding[:10],
            )

    record = state.task(key)
    if current is TaskState.RETRY_WAIT and record is not None:
        return Blocker(
            kind=BlockerKind.RETRY_BACKOFF,
            detail="waiting out retry backoff",
            until=record.retry_at,
        )

    if current is TaskState.READY:
        # Readiness is satisfied; anything holding it now is a resource authority.
        # The reducer does not model admission yet, so this is the honest answer
        # rather than a fabricated one.
        return None

    return None


def project(state: KernelState, *, snapshot_generation: int = 0) -> ProcessStatus:
    """Build the whole projection from durable facts."""
    tasks: list[TaskView] = []
    per_step: dict[str, list[TaskState]] = {}

    for key in sorted(state.materialized):
        current = task_state(state, key)
        record = state.task(key)
        tasks.append(
            TaskView(
                key=str(key),
                step_id=key.step_id,
                item_key=key.item_key,
                state=current,
                generation=record.desired_generation if record else 1,
                attempts=sum(1 for a in state.attempts if a.task_key == key),
                blocker=blocker_for(state, key),
            )
        )
        per_step.setdefault(key.step_id, []).append(current)

    steps = tuple(
        StepView(
            step_id=step_id,
            total=len(states),
            succeeded=states.count(TaskState.SUCCEEDED),
            failed=states.count(TaskState.FAILED),
            cancelled=states.count(TaskState.CANCELLED),
            skipped=states.count(TaskState.SKIPPED),
            running=states.count(TaskState.RUNNING),
            ready=states.count(TaskState.READY),
            waiting=states.count(TaskState.WAITING_DEPENDENCIES),
        )
        for step_id, states in sorted(per_step.items())
    )

    return ProcessStatus(
        snapshot_generation=snapshot_generation,
        tasks=tuple(tasks),
        steps=steps,
    )


__all__ = [
    "PROJECTION_CONTRACT",
    "Blocker",
    "BlockerKind",
    "ProcessStatus",
    "StepView",
    "TaskView",
    "blocker_for",
    "project",
]
