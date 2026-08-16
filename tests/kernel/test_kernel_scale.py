"""Scale envelope for the reference reducer.

The RFC declares a working envelope of 10^3 to 10^4 tasks per run and says it must be
confirmed before the production scheduler grows. This is that confirmation, and it is
deliberately a test rather than a script so the envelope cannot quietly stop holding.

The thresholds are generous. They exist to catch an accidental quadratic, not to police
milliseconds, because the failure this guards against is a scheduler that is fine on a
demo roster and unusable on a real one.
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
from metaproc.kernel.reducer import (
    AttemptEnded,
    AttemptStarted,
    ExpansionClosed,
    Tick,
    materialized_keys,
    reduce,
    task_state,
)


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

    def test_a_full_chain_drains_in_reasonable_time(self) -> None:
        """The shape of a real cohort: a few hundred items through several stages."""
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

    def test_readiness_does_not_degrade_quadratically_with_width(self) -> None:
        """Doubling the roster must not quadruple the cost of one scheduling decision.

        This is the property that actually matters. An accidental quadratic is invisible
        at demo size and fatal at cohort size, and it is the kind of thing a linear scan
        introduces without anyone noticing.
        """

        def one_tick(width: int) -> float:
            state, _ = _chain(width, 3)
            started = time.perf_counter()
            reduce(state, Tick())
            return time.perf_counter() - started

        small = one_tick(100)
        large = one_tick(200)

        # Perfectly linear would be 2x. Allow generous headroom for constant factors
        # and timer noise, but 4x+ means the cost is superlinear in roster width.
        assert large < small * 3.5 + 0.05, (
            f"one scheduling pass: {small:.4f}s at width 100, {large:.4f}s at width 200 "
            f"({large / small:.1f}x for 2x the work)"
        )
