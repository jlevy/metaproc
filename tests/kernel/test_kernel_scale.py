"""Scale envelope for the reference reducer.

The design declares a working envelope of 10^3 to 10^4 tasks per run and says it must be
confirmed before the production scheduler grows. This is that confirmation, and it is
deliberately a test rather than a script so the envelope cannot quietly stop holding.

The thresholds are generous. They exist to catch an accidental quadratic, not to police
milliseconds, because the failure this guards against is a scheduler that is fine on a
small roster and unusable on a full one.

Two things a scale guard has to get right about itself. It must measure above the knee,
where an added quadratic term dominates rather than hiding under constant factors. And
its own bound must sit inside the suite's global per-test timeout, or a regression
arrives as an opaque hang instead of the assertion that would have explained it.
"""

from __future__ import annotations

import time

import pytest

from metaproc.kernel.model import (
    AttemptDisposition,
    ClauseMapping,
    DependencyClause,
    KernelState,
    Requirement,
    StepTemplate,
    TaskKey,
)
from metaproc.kernel.projection import project
from metaproc.kernel.reducer import (
    AttemptEnded,
    AttemptStarted,
    ExpansionClosed,
    Tick,
    materialized_keys,
    reduce,
    task_state,
)


def _time_tick(state: KernelState) -> float:
    started = time.perf_counter()
    reduce(state, Tick())
    return time.perf_counter() - started


def _best_tick(width: int, rounds: int = 3) -> float:
    """Fastest of several passes, so one scheduling hiccup cannot fail the build."""
    state, _ = _chain(width, 3)
    return min(_time_tick(state) for _ in range(rounds))


def _chain(width: int, stages: int) -> tuple[KernelState, tuple[str, ...]]:
    """A `stages`-deep item-aligned chain over `width` items, plus a final barrier."""
    templates: list[StepTemplate] = []
    for i in range(stages):
        clauses = (
            (
                DependencyClause(
                    upstream_step=f"stage{i - 1}",
                    mapping=ClauseMapping.SAME_KEY,
                    requirement=Requirement.SUCCEEDED,
                ),
            )
            if i
            else ()
        )
        templates.append(StepTemplate(step_id=f"stage{i}", expands_over="roster", clauses=clauses))
    templates.append(
        StepTemplate(
            step_id="barrier",
            clauses=(
                DependencyClause(
                    upstream_step=f"stage{stages - 1}",
                    mapping=ClauseMapping.COLLECT_ALL,
                    requirement=Requirement.FINISHED,
                ),
            ),
        )
    )
    keys = tuple(f"item{i:05d}" for i in range(width))
    state = KernelState(templates=tuple(templates))
    for i in range(stages):
        state, _ = reduce(state, ExpansionClosed(step_id=f"stage{i}", keys=keys))
    return state, keys


class TestEnvelope:
    @pytest.mark.parametrize(("width", "stages"), [(200, 5), (500, 5)])
    def test_materialization_stays_within_the_declared_envelope(
        self, width: int, stages: int
    ) -> None:
        state, _ = _chain(width, stages)

        started = time.perf_counter()
        keys = materialized_keys(state)
        elapsed = time.perf_counter() - started

        assert len(keys) == width * stages + 1
        assert elapsed < 1.0, f"materialization took {elapsed:.2f}s for {len(keys)} tasks"

    @pytest.mark.timeout(120)
    def test_a_full_chain_drains_in_reasonable_time(self) -> None:
        """A few hundred items through several stages, the shape of a real workload.

        Carries its own timeout because the assertion bound exceeds the suite default;
        without it a slow runner reports a hang rather than the envelope breach.
        """
        width, stages = 200, 5
        state, keys = _chain(width, stages)

        started = time.perf_counter()
        for stage in range(stages):
            for key in keys:
                task = TaskKey(f"stage{stage}", key)
                attempt = f"a-{stage}-{key}"
                state, _ = reduce(
                    state,
                    AttemptStarted(attempt_id=attempt, task_key=task, generation=1, fence_epoch=0),
                )
                state, _ = reduce(
                    state,
                    AttemptEnded(attempt_id=attempt, disposition=AttemptDisposition.SUCCEEDED),
                )
        elapsed = time.perf_counter() - started

        assert task_state(state, TaskKey("barrier")).value == "ready"
        assert elapsed < 120.0, f"draining {width * stages} tasks took {elapsed:.1f}s"

    @pytest.mark.timeout(120)
    def test_readiness_does_not_degrade_quadratically_with_width(self) -> None:
        """Quadrupling the roster must not cost far more than four times the work.

        Both the width and the span are chosen from measurement rather than taste. A
        2x span could not tell an indexed lookup from a linear scan at these sizes
        (2.6x versus 2.9x, inside the noise of each other), because the quadratic term
        is still small next to the constants. Across 4x the work the two separate
        cleanly: roughly 4x when membership is indexed, roughly 8x when it degrades to
        a scan.
        """
        small = _best_tick(800)
        large = _best_tick(3200)
        ratio = large / small

        assert ratio < 6.0, (
            f"one scheduling pass: {small:.4f}s at width 800, {large:.4f}s at width 3200 "
            f"({ratio:.1f}x for 4x the work; linear is 4x, a scan measures about 8x)"
        )

    @pytest.mark.timeout(120)
    def test_status_projection_stays_within_the_envelope(self) -> None:
        """Status is rendered on demand, so its cost is paid per refresh.

        The reducer's lookups are indexed; the projection has to be too, or a status
        call becomes the slowest thing in the run.
        """
        state, _ = _chain(800, 3)

        started = time.perf_counter()
        status = project(state)
        elapsed = time.perf_counter() - started

        assert len(status.tasks) == 800 * 3 + 1
        assert elapsed < 2.0, f"projecting {len(status.tasks)} tasks took {elapsed:.2f}s"
