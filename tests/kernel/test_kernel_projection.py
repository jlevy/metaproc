"""The status projection, and the blocker vocabulary underneath it.

The blocker is the part that earns its keep, so most of these assert on *which* reason
comes back and which upstream tasks it names. A projection that says "pending" for every
waiting task is the status quo and helps nobody.
"""

from __future__ import annotations

from metaproc.kernel.model import (
    AttemptDisposition,
    ClauseMapping,
    DependencyClause,
    ExpansionRecord,
    ExpansionState,
    KernelState,
    Outcome,
    Requirement,
    StepTemplate,
    TaskKey,
    TaskRecord,
    TaskState,
)
from metaproc.kernel.projection import (
    PROJECTION_CONTRACT,
    BlockerKind,
    blocker_for,
    project,
)
from metaproc.kernel.reducer import AttemptStarted, ExpansionClosed, ForceIssued, reduce
from tests.kernel.test_kernel_invariants import ROSTER, chained_state, close_roster, run_task


class TestBlockerVocabulary:
    def test_a_waiting_task_names_the_upstream_it_waits_on(self) -> None:
        state = close_roster(chained_state())

        blocker = blocker_for(state, TaskKey("interpret", "a"))

        assert blocker is not None
        assert blocker.kind is BlockerKind.DEPENDENCY_PENDING
        assert "measure[a]" in blocker.upstream

    def test_an_unsatisfiable_dependency_outranks_a_pending_one(self) -> None:
        """Terminal beats pending: no amount of waiting fixes a failed upstream.

        Built directly rather than through ``reduce``, because the reducer settles a
        blocked task to ``skipped`` in the same pass, so this precedence is only
        observable to a caller inspecting state before that happens.
        """
        state = KernelState(
            templates=(
                StepTemplate(step_id="alpha", expands_over="roster"),
                StepTemplate(step_id="beta", expands_over="roster"),
                StepTemplate(
                    step_id="consumer",
                    clauses=(
                        DependencyClause(
                            upstream_step="alpha",
                            mapping=ClauseMapping.COLLECT_ALL,
                            requirement=Requirement.SUCCEEDED,
                        ),
                        DependencyClause(
                            upstream_step="beta",
                            mapping=ClauseMapping.COLLECT_ALL,
                            requirement=Requirement.SUCCEEDED,
                        ),
                    ),
                ),
            ),
            expansions=(
                ExpansionRecord(
                    expansion_id="alpha#1",
                    step_id="alpha",
                    generation=1,
                    state=ExpansionState.CLOSED,
                    keys=("a",),
                ),
                ExpansionRecord(
                    expansion_id="beta#1",
                    step_id="beta",
                    generation=1,
                    state=ExpansionState.CLOSED,
                    keys=("a",),
                ),
            ),
            # alpha[a] failed permanently, so the first clause is dead. beta[a] has
            # not run, so the second is merely pending.
            tasks=(
                TaskRecord(
                    task_key=TaskKey("alpha", "a"),
                    outcome=Outcome.FAILED,
                    outcome_generation=1,
                ),
            ),
        )

        blocker = blocker_for(state, TaskKey("consumer"))

        assert blocker is not None
        assert blocker.kind is BlockerKind.DEPENDENCY_UNSATISFIABLE
        assert "alpha" in blocker.detail
        assert blocker.upstream == ("alpha[a]",)

    def test_an_unclosed_roster_is_reported_as_such(self) -> None:
        """Distinguishing 'roster not closed' from 'upstream not finished' is the whole
        reason closure is a first-class fact."""
        state = chained_state()

        blocker = blocker_for(state, TaskKey("summarize"))

        assert blocker is not None
        assert blocker.kind is BlockerKind.EXPANSION_NOT_CLOSED
        assert "interpret" in blocker.detail

    def test_a_task_whose_roster_has_not_arrived_is_unmaterialized(self) -> None:
        state = chained_state()

        blocker = blocker_for(state, TaskKey("measure", "a"))

        assert blocker is not None
        assert blocker.kind is BlockerKind.EXPANSION_NOT_CLOSED

    def test_a_retrying_task_reports_its_wake_time(self) -> None:
        state = close_roster(chained_state())
        state = run_task(state, TaskKey("measure", "a"), AttemptDisposition.RETRYABLE, now=100.0)

        blocker = blocker_for(state, TaskKey("measure", "a"))

        assert blocker is not None
        assert blocker.kind is BlockerKind.RETRY_BACKOFF
        assert blocker.until == 110.0

    def test_a_running_task_has_no_blocker(self) -> None:
        state = close_roster(chained_state())
        state, _ = reduce(
            state,
            AttemptStarted(
                attempt_id="live", task_key=TaskKey("measure", "a"), generation=1, fence_epoch=0
            ),
        )

        assert blocker_for(state, TaskKey("measure", "a")) is None

    def test_a_succeeded_task_has_no_blocker(self) -> None:
        state = close_roster(chained_state())
        state = run_task(state, TaskKey("measure", "a"), AttemptDisposition.SUCCEEDED)

        assert blocker_for(state, TaskKey("measure", "a")) is None


class TestAggregates:
    def test_partial_is_a_step_outcome_never_a_task_outcome(self) -> None:
        """A cohort with most items succeeded and a couple failed is a good result, and
        the projection has to be able to say so."""
        state = close_roster(chained_state())
        state = run_task(state, TaskKey("measure", "a"), AttemptDisposition.PERMANENT)
        for k in ("b", "c"):
            state = run_task(state, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)

        status = project(state)
        measure = next(s for s in status.steps if s.step_id == "measure")

        assert measure.outcome == "partial"
        assert measure.coverage == 2 / 3
        # `partial` is an aggregate; no individual task may ever carry it.
        assert all(t.state.value != "partial" for t in status.tasks)

    def test_a_fully_successful_step_reports_succeeded_and_full_coverage(self) -> None:
        state = close_roster(chained_state())
        for k in ROSTER:
            state = run_task(state, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)

        measure = next(s for s in project(state).steps if s.step_id == "measure")

        assert measure.outcome == "succeeded"
        assert measure.coverage == 1.0

    def test_a_step_still_working_is_incomplete_not_partial(self) -> None:
        state = close_roster(chained_state())
        state = run_task(state, TaskKey("measure", "a"), AttemptDisposition.SUCCEEDED)

        measure = next(s for s in project(state).steps if s.step_id == "measure")

        assert measure.outcome == "incomplete"


class TestProjectionContract:
    def test_the_projection_declares_its_version(self) -> None:
        assert project(close_roster(chained_state())).schema_version == PROJECTION_CONTRACT

    def test_the_projection_is_a_pure_function_of_state(self) -> None:
        """Rebuildable means rebuildable: same facts, same view."""
        state = close_roster(chained_state())
        state = run_task(state, TaskKey("measure", "a"), AttemptDisposition.SUCCEEDED)

        assert project(state) == project(state)

    def test_every_materialized_task_appears_exactly_once(self) -> None:
        state = close_roster(chained_state())
        status = project(state)

        keys = [t.key for t in status.tasks]
        assert len(keys) == len(set(keys)) == len(state.materialized)

    def test_blocked_by_groups_tasks_by_reason(self) -> None:
        state, _ = reduce(chained_state(), ExpansionClosed(step_id="measure", keys=ROSTER))
        status = project(state)

        waiting = status.blocked_by(BlockerKind.EXPANSION_NOT_CLOSED)

        assert {t.key for t in waiting} == {"summarize"}


class TestSkipReasonSurvivesProjection:
    def test_a_skipped_task_carries_its_reason_into_the_view(self) -> None:
        """The durable record has the why; a view that drops it fails the module's own
        bar, and the skipped case is the one operators hit after a partial failure."""
        state = close_roster(chained_state())
        state = run_task(state, TaskKey("measure", "a"), AttemptDisposition.PERMANENT)

        status = project(state)
        skipped = next(t for t in status.tasks if t.state is TaskState.SKIPPED)

        assert skipped.skip_reason is not None
        assert "measure" in skipped.skip_reason


class TestRecomputationIsVisible:
    def test_a_waiting_task_with_a_superseded_commit_reports_generation_changed(self) -> None:
        """After a force, a dependent that already committed is recomputing; that is a
        different answer from ordinary first-time waiting and the projection says so."""
        state = close_roster(chained_state())
        for k in ROSTER:
            state = run_task(state, TaskKey("measure", k), AttemptDisposition.SUCCEEDED)
            state = run_task(state, TaskKey("interpret", k), AttemptDisposition.SUCCEEDED)
        state = run_task(state, TaskKey("summarize"), AttemptDisposition.SUCCEEDED)

        state, _ = reduce(state, ForceIssued(task_key=TaskKey("measure", "a")))

        blocker = blocker_for(state, TaskKey("summarize"))
        assert blocker is not None
        assert blocker.kind is BlockerKind.UPSTREAM_GENERATION_CHANGED

    def test_a_first_time_waiting_task_still_reports_dependency_pending(self) -> None:
        state = close_roster(chained_state())

        blocker = blocker_for(state, TaskKey("interpret", "a"))

        assert blocker is not None
        assert blocker.kind is BlockerKind.DEPENDENCY_PENDING
