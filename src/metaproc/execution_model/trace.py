"""Replay a completed run's durable facts through the reference reducer.

The reducer consumes six events; a finished run's state tree carries the facts they
describe. This module maps one onto the other, which is what lets the reference model
check the engine's live item-scoped semantics instead of only itself: translate the
resolved plan into templates, read the per-task status records back as attempt events,
fold them through ``reduce``, and diff the model's terminal states against what the
engine recorded.

Two stated limitations, both retired by durable attempt records (the durability step in
arch-execution-model.md, § Adoption Path):

- Rosters are reconstructed from task state directories, so an expansion item that never
  produced a task directory is invisible. For an item-aligned chain the head step's
  directories stand in for the roster, which is faithful today because the head runs
  every item.
- A status record keeps only the final attempt count and the final failure, so
  intermediate attempts are replayed as retryable with the final failure class attached.
  Terminal dispositions are exact; per-attempt history is an approximation.

Kept out of the package's ``__init__`` on purpose: the model surface is pure, and this
module imports the engine's plan and state readers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from metaproc.engine.graph import item_aligned_chains
from metaproc.execution_model.model import (
    AttemptDisposition,
    Cardinality,
    ClauseMapping,
    DependencyClause,
    Requirement,
    RetryPolicy,
    RunState,
    StepTemplate,
    TaskKey,
)
from metaproc.execution_model.reducer import (
    AttemptEnded,
    AttemptStarted,
    Event,
    ExpansionClosed,
    reduce,
)
from metaproc.io.state_io import read_status_at
from metaproc.models.plan import ResolvedStep
from metaproc.models.runtime import StatusRecord
from metaproc.paths import STATE_DIR, TASKS_SUBDIR, task_state_dir

_SUCCEEDED_STATES = frozenset({"completed", "cached"})


@dataclass(frozen=True)
class EngineTaskFact:
    """What the engine durably recorded for one task."""

    state: str
    attempts: int


def _chain_heads(steps: Sequence[ResolvedStep]) -> dict[str, str]:
    """Map every member of an item-aligned chain to the chain's head step."""
    heads: dict[str, str] = {}
    for chain in item_aligned_chains(steps):
        for member in chain:
            heads[member] = chain[0]
    return heads


def _retry_policy(step: ResolvedStep) -> RetryPolicy:
    """The model policy equivalent to what the engine enforces for this step.

    A step without a declared retry runs once on the code path, which is the semantic
    the translation must pin: the model's own default of three attempts would let a
    replay retry work the engine never retried.
    """
    fan_out = step.fan_out
    if fan_out is None or fan_out.retry is None:
        return RetryPolicy(max_attempts=1)
    declared = fan_out.retry
    return RetryPolicy(
        max_attempts=declared.max_retries + 1,
        base_seconds=declared.initial_backoff_s,
        multiplier=declared.backoff_multiplier,
        max_seconds=declared.max_backoff_s,
    )


def _clauses(step: ResolvedStep, heads: dict[str, str]) -> tuple[DependencyClause, ...]:
    """Translate one step's edges into four-axis clauses.

    Mirrors the engine's own edge semantics: an ``align: same_key`` need is item-scoped;
    a collected input carries its declared requirement and replaces the plain barrier on
    the same upstream, exactly as ``propagate_failure`` tolerates a failure at a step
    the consumer collects with ``require: finished``.
    """
    collected = {spec.collect: spec for spec in step.inputs.values() if spec.collect is not None}
    clauses: list[DependencyClause] = []
    for need in step.needs:
        if need in collected:
            continue
        aligned = (
            step.fan_out is not None
            and step.fan_out.align == "same_key"
            and heads.get(step.step_id) is not None
            and heads.get(need) == heads.get(step.step_id)
        )
        if aligned:
            clauses.append(
                DependencyClause(
                    upstream_step=need,
                    mapping=ClauseMapping.SAME_KEY,
                    requirement=Requirement.SUCCEEDED,
                )
            )
        else:
            clauses.append(
                DependencyClause(
                    upstream_step=need,
                    mapping=ClauseMapping.COLLECT_ALL,
                    requirement=Requirement.SUCCEEDED,
                    cardinality=Cardinality.ALL,
                )
            )
    for upstream, spec in collected.items():
        requirement = Requirement.FINISHED if spec.require == "finished" else Requirement.SUCCEEDED
        clauses.append(
            DependencyClause(
                upstream_step=upstream,
                mapping=ClauseMapping.COLLECT_ALL,
                requirement=requirement,
                cardinality=Cardinality.ALL,
            )
        )
    return tuple(clauses)


def templates_from_plan(steps: Sequence[ResolvedStep]) -> tuple[StepTemplate, ...]:
    """Translate resolved plan steps into model templates, item semantics included."""
    heads = _chain_heads(steps)
    templates: list[StepTemplate] = []
    for step in steps:
        expands_over = None
        key_space = None
        if step.fan_out is not None:
            head = heads.get(step.step_id, step.step_id)
            head_step = next(s for s in steps if s.step_id == head)
            source = head_step.fan_out.source if head_step.fan_out else head
            expands_over = source
            key_space = source
        templates.append(
            StepTemplate(
                step_id=step.step_id,
                clauses=_clauses(step, heads),
                expands_over=expands_over,
                key_space=key_space,
                retry=_retry_policy(step),
            )
        )
    return tuple(templates)


def _expansion_keys(
    run_dir: Path, steps: Sequence[ResolvedStep], heads: dict[str, str]
) -> dict[str, tuple[str, ...]]:
    """Reconstruct each fan-out step's roster from task state directories.

    Chain members borrow the head's directories: the head runs every roster item, while
    a downstream member only has directories for items that reached it, and reading
    those as the roster would hide exactly the skips the replay must reproduce.
    """
    keys: dict[str, tuple[str, ...]] = {}
    for step in steps:
        if step.fan_out is None:
            continue
        owner = heads.get(step.step_id, step.step_id)
        step_dir = run_dir / STATE_DIR / TASKS_SUBDIR / owner
        found = (
            tuple(sorted(d.name for d in step_dir.iterdir() if d.is_dir()))
            if step_dir.is_dir()
            else ()
        )
        keys[step.step_id] = found
    return keys


def _attempt_events(
    step_id: str, item_key: str | None, record: StatusRecord, policy: RetryPolicy
) -> list[Event]:
    """One task's durable record as its attempt-event sequence.

    Every attempt before the last must have been retryable, or the engine would not
    have retried. The last attempt of a failure is retryable exactly when the budget is
    spent, so the reducer's own ceiling produces the terminal failure; an early stop
    means the engine judged the failure permanent, and the replay says so.
    """
    key = TaskKey(step_id, item_key)
    attempts = max(1, record.attempt)
    if record.state in _SUCCEEDED_STATES:
        final = AttemptDisposition.SUCCEEDED
    elif attempts >= policy.max_attempts:
        final = AttemptDisposition.RETRYABLE
    else:
        final = AttemptDisposition.PERMANENT

    events: list[Event] = []
    for number in range(1, attempts + 1):
        suffix = f"[{item_key}]" if item_key is not None else ""
        attempt_id = f"{step_id}{suffix}#{number}"
        disposition = final if number == attempts else AttemptDisposition.RETRYABLE
        events.append(
            AttemptStarted(attempt_id=attempt_id, task_key=key, generation=1, fence_epoch=0)
        )
        events.append(
            AttemptEnded(
                attempt_id=attempt_id,
                disposition=disposition,
                failure_category=record.failure_class,
            )
        )
    return events


def events_from_run(run_dir: Path, steps: Sequence[ResolvedStep]) -> tuple[Event, ...]:
    """Read a completed run tree back as the reducer's event sequence."""
    heads = _chain_heads(steps)
    rosters = _expansion_keys(run_dir, steps, heads)

    events: list[Event] = []
    for step in steps:
        if step.fan_out is None:
            continue
        head = heads.get(step.step_id, step.step_id)
        head_step = next(s for s in steps if s.step_id == head)
        source = head_step.fan_out.source if head_step.fan_out else head
        events.append(
            ExpansionClosed(step_id=step.step_id, keys=rosters[step.step_id], key_space=source)
        )

    for step in steps:
        policy = _retry_policy(step)
        if step.fan_out is None:
            record = read_status_at(run_dir / STATE_DIR / TASKS_SUBDIR / step.step_id)
            if record is not None:
                events.extend(_attempt_events(step.step_id, None, record, policy))
            continue
        for item_key in rosters[step.step_id]:
            record = read_status_at(task_state_dir(run_dir, step.step_id, item_key))
            if record is not None:
                events.extend(_attempt_events(step.step_id, item_key, record, policy))
    return tuple(events)


def engine_task_facts(
    run_dir: Path, steps: Sequence[ResolvedStep]
) -> dict[TaskKey, EngineTaskFact]:
    """Every task the engine recorded, by key, for diffing against the replayed state."""
    heads = _chain_heads(steps)
    rosters = _expansion_keys(run_dir, steps, heads)
    facts: dict[TaskKey, EngineTaskFact] = {}
    for step in steps:
        if step.fan_out is None:
            record = read_status_at(run_dir / STATE_DIR / TASKS_SUBDIR / step.step_id)
            if record is not None:
                facts[TaskKey(step.step_id)] = EngineTaskFact(record.state, record.attempt)
            continue
        for item_key in rosters[step.step_id]:
            record = read_status_at(task_state_dir(run_dir, step.step_id, item_key))
            if record is not None:
                facts[TaskKey(step.step_id, item_key)] = EngineTaskFact(
                    record.state, record.attempt
                )
    return facts


def replay_run(run_dir: Path, steps: Sequence[ResolvedStep]) -> RunState:
    """Fold a completed run's facts through the reducer and return the final state."""
    state = RunState(templates=templates_from_plan(steps))
    for index, event in enumerate(events_from_run(run_dir, steps)):
        state, _ = reduce(state, event, now=float(index))
    return state
