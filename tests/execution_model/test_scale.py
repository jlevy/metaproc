"""Scale envelope for the reference reducer.

The design declares a working envelope of 10^3 to 10^4 tasks per run and says it must be
confirmed before the production scheduler grows. This is that confirmation, and it is
deliberately a test rather than a script so the envelope cannot quietly stop holding.

The wall-clock thresholds are generous. They exist to catch an unusable implementation,
not to police milliseconds. The known quadratic hazard has a deterministic guard: count
the equality work performed by aligned roster membership at production width. That
distinguishes an index from a scan without making correctness depend on CI load.
"""

from __future__ import annotations

import time
from typing import ClassVar, override

import pytest

from metaproc.execution_model.model import (
    AttemptDisposition,
    ClauseMapping,
    DependencyClause,
    ExpansionRecord,
    ExpansionState,
    Requirement,
    RunState,
    StepTemplate,
    TaskKey,
)
from metaproc.execution_model.projection import project
from metaproc.execution_model.reducer import (
    AttemptEnded,
    AttemptStarted,
    ExpansionClosed,
    materialized_keys,
    reduce,
    related_keys,
    task_state,
)

# Large enough that a scan's quadratic work cannot hide behind constant factors.
_ALIGNED_ROSTER_WIDTH = 3_200

# Successful hash lookups normally compare once; allow a few collisions per item.
_MAX_EQUALITY_COMPARISONS_PER_LOOKUP = 4


class _ComparisonCountingKey(str):
    """String key that exposes the amount of equality work a lookup performs."""

    comparisons: ClassVar[int] = 0
    __hash__ = str.__hash__

    @override
    def __eq__(self, other: object) -> bool:
        _ComparisonCountingKey.comparisons += 1
        return str.__eq__(self, other)


class _IndexConstructionCountingKey(str):
    """String key that exposes repeated membership-index construction."""

    hash_calls: ClassVar[int] = 0

    @override
    def __hash__(self) -> int:
        _IndexConstructionCountingKey.hash_calls += 1
        return str.__hash__(self)


def _aligned_membership_comparisons(width: int) -> int:
    """Equality comparisons needed to resolve one aligned lookup per roster item."""
    template = StepTemplate(step_id="upstream", expands_over="roster")
    clause = DependencyClause(
        upstream_step="upstream",
        mapping=ClauseMapping.SAME_KEY,
        requirement=Requirement.SUCCEEDED,
    )
    keys = tuple(_ComparisonCountingKey(f"item{i:05d}") for i in range(width))
    state, _ = reduce(
        RunState(templates=(template,)),
        ExpansionClosed(step_id="upstream", keys=keys),
    )
    expansion = state.expansion_for("upstream")
    assert expansion is not None
    _ = expansion.key_set  # Build the index before measuring lookup work.

    _ComparisonCountingKey.comparisons = 0
    for key in keys:
        probe = _ComparisonCountingKey(key)
        related = related_keys(state, TaskKey("downstream", probe), clause)
        assert related is not None and len(related) == 1
    return _ComparisonCountingKey.comparisons


def _chain(width: int, stages: int) -> tuple[RunState, tuple[str, ...]]:
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
    state = RunState(templates=tuple(templates))
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

    def test_aligned_roster_membership_stays_linear(self) -> None:
        """One aligned lookup per item must perform O(width) equality work in total."""
        width = _ALIGNED_ROSTER_WIDTH
        comparisons = _aligned_membership_comparisons(width)
        comparison_ceiling = width * _MAX_EQUALITY_COMPARISONS_PER_LOOKUP

        # A hash index normally performs one equality check per successful lookup. The
        # lower bound proves the instrument is live, the allowance covers collisions,
        # and a tuple scan performs about width**2 / 2.
        assert width <= comparisons <= comparison_ceiling, (
            f"{comparisons:,} equality comparisons for {width:,} aligned lookups; "
            f"expected between {width:,} and {comparison_ceiling:,}"
        )

    def test_aligned_roster_membership_index_is_memoized(self) -> None:
        """Repeated aligned lookups must reuse one constructed membership index."""
        keys = tuple(_IndexConstructionCountingKey(f"item{i:05d}") for i in range(200))
        expansion = ExpansionRecord(
            expansion_id="upstream:1",
            step_id="upstream",
            generation=1,
            state=ExpansionState.CLOSED,
            keys=keys,
        )

        _IndexConstructionCountingKey.hash_calls = 0
        first_index = expansion.key_set
        construction_hash_calls = _IndexConstructionCountingKey.hash_calls

        assert construction_hash_calls > 0
        for _ in range(32):
            assert expansion.key_set is first_index
        assert _IndexConstructionCountingKey.hash_calls == construction_hash_calls

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
