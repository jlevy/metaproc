"""Invariants the execution model must hold.

Each test names the invariant it proves from the execution-model design and architecture docs. These
are the semantics every later scheduler phase is checked against, so a failure here means
a contract changed, not that a test needs updating.
"""

from __future__ import annotations

import pytest

from metaproc.kernel.model import (
    AttemptDisposition,
    ClauseMapping,
    DependencyClause,
    ExpansionState,
    KernelState,
    Outcome,
    Requirement,
    RetryPolicy,
    StepTemplate,
    TaskKey,
    TaskState,
)
from metaproc.kernel.reducer import (
    AttemptEnded,
    AttemptStarted,
    CancelAttempt,
    DispatchAttempt,
    ExpansionClosed,
    ForceIssued,
    ScheduleRetry,
    Tick,
    clause_status,
    reduce,
    related_keys,
    task_state,
)

ROSTER = ("a", "b", "c")


def _same_key(upstream: str, requirement: Requirement = Requirement.SUCCEEDED) -> DependencyClause:
    return DependencyClause(
        upstream_step=upstream, mapping=ClauseMapping.SAME_KEY, requirement=requirement
    )


def _collect(upstream: str, requirement: Requirement) -> DependencyClause:
    return DependencyClause(
        upstream_step=upstream, mapping=ClauseMapping.COLLECT_ALL, requirement=requirement
    )


def chained_state() -> KernelState:
    """measure[k] -> interpret[k], both over one roster, plus a collect-all barrier."""
    return KernelState(
        templates=(
            StepTemplate(step_id="measure", expands_over="roster", key_space="k/v1"),
            StepTemplate(
                step_id="interpret",
                expands_over="roster",
                key_space="k/v1",
                clauses=(_same_key("measure"),),
            ),
            StepTemplate(
                step_id="summarize",
                clauses=(_collect("interpret", Requirement.FINISHED),),
            ),
        )
    )


def close_roster(state: KernelState, keys: tuple[str, ...] = ROSTER) -> KernelState:
    for step in ("measure", "interpret"):
        state, _ = reduce(state, ExpansionClosed(step_id=step, keys=keys, key_space="k/v1"))
    return state


def run_task(
    state: KernelState,
    key: TaskKey,
    disposition: AttemptDisposition,
    *,
    attempt_id: str | None = None,
    generation: int = 1,
    fence_epoch: int = 0,
    now: float = 0.0,
) -> KernelState:
    aid = attempt_id or f"att-{key}-{generation}"
    state, _ = reduce(
        state,
        AttemptStarted(
            attempt_id=aid, task_key=key, generation=generation, fence_epoch=fence_epoch
        ),
        now=now,
    )
    state, _ = reduce(state, AttemptEnded(attempt_id=aid, disposition=disposition), now=now)
    return state


class TestClauseSatisfaction:
    def test_no_task_is_ready_before_its_clauses_are_satisfied(self) -> None:
        """Invariant 1."""
        state = close_roster(chained_state())

        assert task_state(state, TaskKey("interpret", "a")) is TaskState.WAITING_DEPENDENCIES

        state = run_task(state, TaskKey("measure", "a"), AttemptDisposition.SUCCEEDED)

        assert task_state(state, TaskKey("interpret", "a")) is TaskState.READY

    def test_ready_tasks_are_proposed_for_dispatch(self) -> None:
        state = close_roster(chained_state())
        _, commands = reduce(state, Tick())

        dispatched = {c.task_key for c in commands if isinstance(c, DispatchAttempt)}

        assert dispatched == {TaskKey("measure", k) for k in ROSTER}


class TestExpansionClosure:
    def test_fan_in_does_not_fire_over_an_unclosed_expansion(self) -> None:
        """Invariant 2: the premature-barrier bug.

        Every visible upstream task is terminal, yet the roster is still open, so the
        barrier must not fire. Treating "no visible incomplete work" as completion is
        exactly what an event-driven scheduler gets wrong.
        """
        state = KernelState(
            templates=(
                StepTemplate(step_id="measure", expands_over="roster"),
                StepTemplate(
                    step_id="summarize", clauses=(_collect("measure", Requirement.FINISHED),)
                ),
            )
        )

        assert (
            clause_status(state, TaskKey("summarize"), _collect("measure", Requirement.FINISHED))
            == "pending"
        )
        assert task_state(state, TaskKey("summarize")) is TaskState.WAITING_DEPENDENCIES

    def test_barrier_fires_once_the_roster_closes_and_all_are_terminal(self) -> None:
        state = close_roster(chained_state())
        for k in ROSTER:
            state = run_task(state, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)
            state = run_task(state, TaskKey("interpret", k), AttemptDisposition.SUCCEEDED)

        assert task_state(state, TaskKey("summarize")) is TaskState.READY

    def test_empty_closed_roster_satisfies_all_and_terminates(self) -> None:
        """Invariant 11."""
        state = close_roster(chained_state(), keys=())

        assert task_state(state, TaskKey("summarize")) is TaskState.READY

    def test_duplicate_keys_fail_materialization_deterministically(self) -> None:
        """Invariant 12."""
        state, _ = reduce(chained_state(), ExpansionClosed(step_id="measure", keys=("a", "b", "a")))
        expansion = state.expansion_for("measure")

        assert expansion is not None
        assert expansion.state is ExpansionState.FAILED
        assert "duplicate keys" in (expansion.failure_reason or "")
        assert task_state(state, TaskKey("measure", "a")) is TaskState.UNMATERIALIZED


class TestCommitLinearization:
    def test_at_most_one_commit_per_task_generation(self) -> None:
        """Invariant 3."""
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state = run_task(state, key, AttemptDisposition.SUCCEEDED, attempt_id="first")

        state, _ = reduce(
            state, AttemptStarted(attempt_id="second", task_key=key, generation=1, fence_epoch=0)
        )
        state, _ = reduce(
            state, AttemptEnded(attempt_id="second", disposition=AttemptDisposition.SUCCEEDED)
        )

        commits = [c for c in state.commits if c.task_key == key]
        assert len(commits) == 1
        assert commits[0].attempt_id == "first"

    def test_a_stale_attempt_cannot_commit_after_a_newer_generation_is_granted(self) -> None:
        """Invariant 4: the fencing case.

        A partitioned worker finishes successfully after its generation was superseded.
        Exit code zero does not entitle it to publish.
        """
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")

        state, _ = reduce(
            state, AttemptStarted(attempt_id="slow", task_key=key, generation=1, fence_epoch=0)
        )
        state, _ = reduce(state, ForceIssued(task_key=key))
        state, _ = reduce(
            state, AttemptEnded(attempt_id="slow", disposition=AttemptDisposition.SUCCEEDED)
        )

        assert state.commit_for(key, 1) is None
        assert state.commit_for(key, 2) is None
        record = state.task(key)
        assert record is not None
        assert record.outcome is None

    def test_a_superseded_live_attempt_is_proposed_for_cancellation(self) -> None:
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state, _ = reduce(
            state, AttemptStarted(attempt_id="slow", task_key=key, generation=1, fence_epoch=0)
        )
        state, commands = reduce(state, ForceIssued(task_key=key))

        assert CancelAttempt(attempt_id="slow", reason="superseded generation") in commands


class TestFailurePropagation:
    def test_an_unrelated_items_failure_does_not_block_unrelated_descendants(self) -> None:
        """Invariant 7. This is the whole point of item-scoped edges."""
        state = close_roster(chained_state())
        state = run_task(state, TaskKey("measure", "a"), AttemptDisposition.PERMANENT)
        state = run_task(state, TaskKey("measure", "b"), AttemptDisposition.SUCCEEDED)

        assert task_state(state, TaskKey("interpret", "b")) is TaskState.READY

    def test_an_aligned_failure_blocks_exactly_the_mapped_descendant(self) -> None:
        """Invariant 8."""
        state = close_roster(chained_state())
        state = run_task(state, TaskKey("measure", "a"), AttemptDisposition.PERMANENT)

        assert task_state(state, TaskKey("interpret", "a")) is TaskState.SKIPPED
        assert task_state(state, TaskKey("interpret", "c")) is TaskState.WAITING_DEPENDENCIES

    def test_finished_requirement_accepts_failures_where_succeeded_would_not(self) -> None:
        """Partial success is a normal product state, so a barrier can require only that
        every upstream reached a terminal outcome."""
        state = close_roster(chained_state())
        for k in ROSTER:
            state = run_task(state, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)
        state = run_task(state, TaskKey("interpret", "a"), AttemptDisposition.PERMANENT)
        for k in ("b", "c"):
            state = run_task(state, TaskKey("interpret", k), AttemptDisposition.SUCCEEDED)

        assert task_state(state, TaskKey("summarize")) is TaskState.READY
        assert (
            clause_status(state, TaskKey("summarize"), _collect("interpret", Requirement.SUCCEEDED))
            == "dead"
        )


class TestRetry:
    def test_retry_backoff_is_deterministic_and_survives_replay(self) -> None:
        """Invariant 6: no jitter, no wall clock, so replay is exact."""
        policy = RetryPolicy()
        assert policy.delay_for(1) == 10.0
        assert policy.delay_for(2) == 20.0
        assert policy.delay_for(99) == 600.0

    def test_a_step_can_carry_its_own_retry_policy(self) -> None:
        """Policy is data, so a spec with different retry settings is replayable."""
        patient = RetryPolicy(max_attempts=5, base_seconds=1.0, multiplier=3.0)
        assert patient.delay_for(1) == 1.0
        assert patient.delay_for(3) == 9.0
        assert patient.max_attempts == 5

    def test_a_retryable_attempt_schedules_a_retry_rather_than_failing(self) -> None:
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state = run_task(state, key, AttemptDisposition.RETRYABLE, now=100.0)

        assert task_state(state, key) is TaskState.RETRY_WAIT
        _, commands = reduce(state, Tick(), now=100.0)
        assert ScheduleRetry(task_key=key, at=110.0) in commands

    def test_a_task_becomes_ready_again_once_the_backoff_elapses(self) -> None:
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state = run_task(state, key, AttemptDisposition.RETRYABLE, now=100.0)

        state, _ = reduce(state, Tick(), now=111.0)

        assert task_state(state, key) is TaskState.READY

    def test_retries_stop_at_the_attempt_ceiling(self) -> None:
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        for i in range(3):
            state = run_task(
                state, key, AttemptDisposition.RETRYABLE, attempt_id=f"a{i}", now=100.0 + i
            )

        assert task_state(state, key) is TaskState.FAILED


class TestForce:
    def test_force_follows_the_same_mapping_as_readiness(self) -> None:
        """Invariant 10: same_key force invalidates only the same key's descendant."""
        state = close_roster(chained_state())
        for k in ROSTER:
            state = run_task(state, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)
            state = run_task(state, TaskKey("interpret", k), AttemptDisposition.SUCCEEDED)

        state, _ = reduce(state, ForceIssued(task_key=TaskKey("measure", "a")))

        assert task_state(state, TaskKey("measure", "a")) is TaskState.READY
        assert task_state(state, TaskKey("interpret", "a")) is TaskState.WAITING_DEPENDENCIES
        assert task_state(state, TaskKey("interpret", "b")) is TaskState.SUCCEEDED
        assert task_state(state, TaskKey("interpret", "c")) is TaskState.SUCCEEDED

    def test_collect_all_invalidation_reaches_the_fan_in(self) -> None:
        """Invariant 9."""
        state = close_roster(chained_state())
        for k in ROSTER:
            state = run_task(state, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)
            state = run_task(state, TaskKey("interpret", k), AttemptDisposition.SUCCEEDED)
        state = run_task(state, TaskKey("summarize"), AttemptDisposition.SUCCEEDED)
        assert task_state(state, TaskKey("summarize")) is TaskState.SUCCEEDED

        state, _ = reduce(state, ForceIssued(task_key=TaskKey("interpret", "a")))

        assert task_state(state, TaskKey("summarize")) is TaskState.WAITING_DEPENDENCIES


class TestReplayDeterminism:
    def test_the_same_facts_in_different_orders_reach_the_same_state(self) -> None:
        """Invariant 5. Independent items commute, so state is a function of facts."""
        forward = close_roster(chained_state())
        for k in ROSTER:
            forward = run_task(forward, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)

        backward = close_roster(chained_state())
        for k in reversed(ROSTER):
            backward = run_task(backward, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)

        assert forward.tasks == backward.tasks
        assert forward.commits == backward.commits
        assert forward.expansions == backward.expansions

    @pytest.mark.parametrize("disposition", list(AttemptDisposition))
    def test_re_applying_a_terminal_attempt_event_changes_nothing(
        self, disposition: AttemptDisposition
    ) -> None:
        """Redelivery is normal in a distributed system, so it must be a no-op."""
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state = run_task(state, key, disposition, attempt_id="once", now=50.0)

        replayed, _ = reduce(
            state, AttemptEnded(attempt_id="once", disposition=disposition), now=50.0
        )

        assert replayed.tasks == state.tasks
        assert replayed.commits == state.commits
        assert replayed.attempts == state.attempts


class TestFencingCoversEveryDisposition:
    """A superseded attempt may not touch the task record, whatever its disposition.

    The commit path was fenced from the start; these pin the rest. Both were found by
    review repro, not by the original suite, because the original tests only exercised
    fencing through the SUCCEEDED path.
    """

    def test_a_stale_retryable_does_not_delay_the_new_generation(self) -> None:
        """The old attempt's backoff must not put the forced re-run into retry_wait."""
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state, _ = reduce(
            state,
            AttemptStarted(attempt_id="slow", task_key=key, generation=1, fence_epoch=0),
            now=100.0,
        )
        state, _ = reduce(state, ForceIssued(task_key=key))

        state, _ = reduce(
            state,
            AttemptEnded(attempt_id="slow", disposition=AttemptDisposition.RETRYABLE),
            now=100.0,
        )

        record = state.task(key)
        assert record is not None
        assert record.retry_at is None
        assert task_state(state, key) is TaskState.READY

    def test_a_stale_permanent_does_not_mutate_the_record(self) -> None:
        """History is append-only AND quarantined: the attempt is recorded, the task
        record is untouched."""
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state, _ = reduce(
            state,
            AttemptStarted(attempt_id="slow", task_key=key, generation=1, fence_epoch=0),
        )
        state, _ = reduce(state, ForceIssued(task_key=key))
        state = run_task(
            state,
            key,
            AttemptDisposition.SUCCEEDED,
            attempt_id="fresh",
            generation=2,
            fence_epoch=1,
        )

        state, _ = reduce(
            state, AttemptEnded(attempt_id="slow", disposition=AttemptDisposition.PERMANENT)
        )

        record = state.task(key)
        assert record is not None
        assert record.outcome is Outcome.SUCCEEDED
        assert record.outcome_generation == 2
        stale = state.attempt("slow")
        assert stale is not None and stale.disposition is AttemptDisposition.PERMANENT

    def test_a_stale_lost_does_not_schedule_anything(self) -> None:
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state, _ = reduce(
            state,
            AttemptStarted(attempt_id="gone", task_key=key, generation=1, fence_epoch=0),
        )
        state, _ = reduce(state, ForceIssued(task_key=key))

        state, commands = reduce(
            state, AttemptEnded(attempt_id="gone", disposition=AttemptDisposition.LOST)
        )

        record = state.task(key)
        assert record is not None and record.retry_at is None
        assert not any(isinstance(c, ScheduleRetry) for c in commands)


class TestForceIsTransitive:
    def test_force_invalidates_grandchildren_along_the_mapping(self) -> None:
        """Invariant 10 is about paths, not edges: summarize consumed interpret, which
        consumed the forced task, so its commit is over a superseded world."""
        state = close_roster(chained_state())
        for k in ROSTER:
            state = run_task(state, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)
            state = run_task(state, TaskKey("interpret", k), AttemptDisposition.SUCCEEDED)
        state = run_task(state, TaskKey("summarize"), AttemptDisposition.SUCCEEDED)

        state, _ = reduce(state, ForceIssued(task_key=TaskKey("measure", "a")))

        assert task_state(state, TaskKey("measure", "a")) is TaskState.READY
        assert task_state(state, TaskKey("interpret", "a")) is TaskState.WAITING_DEPENDENCIES
        assert task_state(state, TaskKey("summarize")) is TaskState.WAITING_DEPENDENCIES

    def test_force_still_leaves_unrelated_items_alone(self) -> None:
        """Transitivity must not become blast radius: same-key narrowing holds per hop."""
        state = close_roster(chained_state())
        for k in ROSTER:
            state = run_task(state, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)
            state = run_task(state, TaskKey("interpret", k), AttemptDisposition.SUCCEEDED)

        state, _ = reduce(state, ForceIssued(task_key=TaskKey("measure", "a")))

        assert task_state(state, TaskKey("measure", "b")) is TaskState.SUCCEEDED
        assert task_state(state, TaskKey("interpret", "b")) is TaskState.SUCCEEDED
        assert task_state(state, TaskKey("interpret", "c")) is TaskState.SUCCEEDED


class TestBroadcastRequiresASingletonUpstream:
    def test_broadcast_over_a_multi_key_expansion_is_deterministically_dead(self) -> None:
        """There is no principled 'the one' item of a fan-out; silently picking the
        first defines arbitrary semantics for everything checked against this model."""
        state = KernelState(
            templates=(
                StepTemplate(step_id="wide", expands_over="roster"),
                StepTemplate(
                    step_id="consumer",
                    clauses=(
                        DependencyClause(
                            upstream_step="wide",
                            mapping=ClauseMapping.BROADCAST,
                            requirement=Requirement.SUCCEEDED,
                        ),
                    ),
                ),
            )
        )
        state, _ = reduce(state, ExpansionClosed(step_id="wide", keys=("a", "b")))

        consumer = state.template("consumer")
        assert consumer is not None
        assert clause_status(state, TaskKey("consumer"), consumer.clauses[0]) == "dead"
        assert task_state(state, TaskKey("consumer")) is TaskState.SKIPPED

    def test_broadcast_over_a_singleton_expansion_works(self) -> None:
        state = KernelState(
            templates=(
                StepTemplate(step_id="one", expands_over="roster"),
                StepTemplate(
                    step_id="consumer",
                    clauses=(
                        DependencyClause(
                            upstream_step="one",
                            mapping=ClauseMapping.BROADCAST,
                            requirement=Requirement.SUCCEEDED,
                        ),
                    ),
                ),
            )
        )
        state, _ = reduce(state, ExpansionClosed(step_id="one", keys=("only",)))
        state = run_task(state, TaskKey("one", "only"), AttemptDisposition.SUCCEEDED)

        assert task_state(state, TaskKey("consumer")) is TaskState.READY


class TestSameGenerationSettlementIsFenced:
    """A duplicate attempt at the current generation must not rewrite a settled task.

    Fencing by epoch and generation stops *superseded* attempts. A second attempt at the
    same generation passes that check, so the guard has to be the settled record itself,
    or the final outcome depends on which duplicate is reaped first.
    """

    def test_a_duplicate_cancelled_does_not_overwrite_a_commit(self) -> None:
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state, _ = reduce(
            state, AttemptStarted(attempt_id="a1", task_key=key, generation=1, fence_epoch=0)
        )
        state, _ = reduce(
            state, AttemptStarted(attempt_id="a2", task_key=key, generation=1, fence_epoch=0)
        )
        state, _ = reduce(
            state, AttemptEnded(attempt_id="a1", disposition=AttemptDisposition.SUCCEEDED)
        )

        state, _ = reduce(
            state, AttemptEnded(attempt_id="a2", disposition=AttemptDisposition.CANCELLED)
        )

        record = state.task(key)
        assert record is not None
        assert record.outcome is Outcome.SUCCEEDED
        assert task_state(state, key) is TaskState.SUCCEEDED

    def test_a_duplicate_permanent_does_not_overwrite_a_commit(self) -> None:
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state, _ = reduce(
            state, AttemptStarted(attempt_id="b1", task_key=key, generation=1, fence_epoch=0)
        )
        state, _ = reduce(
            state, AttemptStarted(attempt_id="b2", task_key=key, generation=1, fence_epoch=0)
        )
        state, _ = reduce(
            state, AttemptEnded(attempt_id="b1", disposition=AttemptDisposition.SUCCEEDED)
        )

        state, _ = reduce(
            state, AttemptEnded(attempt_id="b2", disposition=AttemptDisposition.PERMANENT)
        )

        record = state.task(key)
        assert record is not None and record.outcome is Outcome.SUCCEEDED

    def test_settlement_is_order_independent(self) -> None:
        """Invariant 5 stated over duplicates: whichever arrives first, same end state."""

        def drain(order: tuple[str, ...]) -> KernelState:
            state = close_roster(chained_state())
            key = TaskKey("measure", "a")
            for attempt in ("x1", "x2"):
                state, _ = reduce(
                    state,
                    AttemptStarted(attempt_id=attempt, task_key=key, generation=1, fence_epoch=0),
                )
            dispositions = {
                "x1": AttemptDisposition.SUCCEEDED,
                "x2": AttemptDisposition.PERMANENT,
            }
            for attempt in order:
                state, _ = reduce(
                    state, AttemptEnded(attempt_id=attempt, disposition=dispositions[attempt])
                )
            return state

        assert drain(("x1", "x2")).tasks == drain(("x2", "x1")).tasks


class TestAttemptStartedIsIdempotent:
    def test_redelivering_a_start_does_not_resurrect_a_terminal_attempt(self) -> None:
        """Redelivery is normal in a distributed system; the append-only contract says
        a terminal attempt stays terminal."""
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state = run_task(state, key, AttemptDisposition.RETRYABLE, attempt_id="once", now=100.0)

        state, _ = reduce(
            state,
            AttemptStarted(attempt_id="once", task_key=key, generation=1, fence_epoch=0),
            now=101.0,
        )

        attempt = state.attempt("once")
        assert attempt is not None and attempt.disposition is AttemptDisposition.RETRYABLE
        record = state.task(key)
        assert record is not None and record.retry_at == 110.0
        assert task_state(state, key) is TaskState.RETRY_WAIT

    def test_a_fresh_attempt_id_still_starts_normally(self) -> None:
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state = run_task(state, key, AttemptDisposition.RETRYABLE, attempt_id="first", now=100.0)

        state, _ = reduce(
            state,
            AttemptStarted(attempt_id="second", task_key=key, generation=1, fence_epoch=0),
            now=200.0,
        )

        assert task_state(state, key) is TaskState.RUNNING


class TestMissingAndUnknownUpstreams:
    def test_a_same_key_item_absent_from_the_upstream_roster_is_dead(self) -> None:
        """Rosters can skew between steps; the item with no counterpart can never run."""
        state = KernelState(
            templates=(
                StepTemplate(step_id="up", expands_over="roster"),
                StepTemplate(
                    step_id="down",
                    expands_over="roster",
                    clauses=(_same_key("up"),),
                ),
            )
        )
        state, _ = reduce(state, ExpansionClosed(step_id="up", keys=("a", "b")))
        state, _ = reduce(state, ExpansionClosed(step_id="down", keys=("a", "b", "x")))

        assert task_state(state, TaskKey("down", "x")) is TaskState.SKIPPED
        assert task_state(state, TaskKey("down", "a")) is TaskState.WAITING_DEPENDENCIES

    def test_a_clause_naming_an_unknown_step_is_dead(self) -> None:
        state = KernelState(
            templates=(
                StepTemplate(
                    step_id="consumer",
                    clauses=(
                        DependencyClause(
                            upstream_step="typo",
                            mapping=ClauseMapping.COLLECT_ALL,
                            requirement=Requirement.SUCCEEDED,
                        ),
                    ),
                ),
            )
        )
        state, _ = reduce(state, Tick())

        assert task_state(state, TaskKey("consumer")) is TaskState.SKIPPED


class TestBlockedSettlementReachesFixpoint:
    def test_a_multi_hop_cascade_settles_regardless_of_step_naming(self) -> None:
        """Names sorted so each dependent precedes its upstream, the order that used to
        leave the tail unsettled."""

        def chain_state() -> KernelState:
            return KernelState(
                templates=(
                    StepTemplate(step_id="c_root"),
                    StepTemplate(
                        step_id="b_mid",
                        clauses=(_collect("c_root", Requirement.SUCCEEDED),),
                    ),
                    StepTemplate(
                        step_id="a_leaf",
                        clauses=(_collect("b_mid", Requirement.SUCCEEDED),),
                    ),
                )
            )

        state = chain_state()
        state, _ = reduce(
            state,
            AttemptStarted(attempt_id="r", task_key=TaskKey("c_root"), generation=1, fence_epoch=0),
        )
        state, _ = reduce(
            state, AttemptEnded(attempt_id="r", disposition=AttemptDisposition.PERMANENT)
        )

        assert task_state(state, TaskKey("b_mid")) is TaskState.SKIPPED
        assert task_state(state, TaskKey("a_leaf")) is TaskState.SKIPPED


class TestBroadcastArity:
    def _broadcast_state(self, keys: tuple[str, ...]) -> KernelState:
        state = KernelState(
            templates=(
                StepTemplate(step_id="wide", expands_over="roster"),
                StepTemplate(
                    step_id="consumer",
                    clauses=(
                        DependencyClause(
                            upstream_step="wide",
                            mapping=ClauseMapping.BROADCAST,
                            requirement=Requirement.SUCCEEDED,
                        ),
                    ),
                ),
            )
        )
        state, _ = reduce(state, ExpansionClosed(step_id="wide", keys=keys))
        return state

    def test_broadcast_over_an_empty_closed_roster_is_dead(self) -> None:
        """Zero items has no principled "the one" either, so it cannot be vacuous."""
        assert task_state(self._broadcast_state(()), TaskKey("consumer")) is TaskState.SKIPPED

    def test_related_keys_itself_refuses_a_wide_broadcast(self) -> None:
        """The rule belongs in the mapping, so every consumer inherits it rather than
        each caller re-implementing the width check."""
        state = self._broadcast_state(("a", "b"))
        template = state.template("consumer")
        assert template is not None

        assert related_keys(state, TaskKey("consumer"), template.clauses[0]) == ()

    def test_a_singleton_broadcast_still_binds(self) -> None:
        state = self._broadcast_state(("only",))
        state = run_task(state, TaskKey("wide", "only"), AttemptDisposition.SUCCEEDED)

        assert task_state(state, TaskKey("consumer")) is TaskState.READY


class TestSupersededIsOnePredicate:
    def test_a_stale_generation_live_attempt_is_cancelled(self) -> None:
        """Fencing and cancellation must agree, or an attempt runs to completion holding
        capacity and then has its result discarded."""
        state = close_roster(chained_state())
        key = TaskKey("measure", "a")
        state, _ = reduce(
            state,
            AttemptStarted(attempt_id="odd", task_key=key, generation=5, fence_epoch=0),
        )

        _, commands = reduce(state, Tick())

        assert any(isinstance(c, CancelAttempt) and c.attempt_id == "odd" for c in commands)
