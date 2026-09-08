"""The reference model checked against the real engine, on real process specs.

Every other test in this package checks the model against itself. That proves internal
consistency and nothing about whether the model describes Metaproc, so on its own it is
speculative design: a specification that could be perfectly coherent and still disagree
with the engine it claims to specify.

These tests close that gap for the degenerate case the design calls out first. They take
a spec shipped in this repository, resolve it through the engine's own ``build_plan``,
translate the resulting steps into the model's templates, and assert the model schedules
exactly what the engine's ``topo_sort`` level walk schedules.

Degenerate means step-scoped edges, which is what today's specs use. That is the point:
before item-aligned semantics can be trusted to replace the level walk, the model has to
reproduce the level walk. Item-scoped behaviour is covered by the invariant suite; this
is the half that ties the model to reality.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from metaproc.engine.graph import topo_sort
from metaproc.execution_model.model import (
    AttemptDisposition,
    Cardinality,
    ClauseMapping,
    DependencyClause,
    Requirement,
    RunState,
    StepTemplate,
    TaskKey,
    TaskState,
)
from metaproc.execution_model.reducer import (
    AttemptEnded,
    AttemptStarted,
    DispatchAttempt,
    Tick,
    reduce,
    task_state,
)
from metaproc.models.plan import ResolvedStep

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _templates_from(steps: list[ResolvedStep]) -> tuple[StepTemplate, ...]:
    """Translate resolved plan steps into model templates.

    A `needs` edge on a non-expanding step is a step-scoped barrier, which in clause
    terms is `collect_all` over a singleton upstream requiring success. Expressing the
    engine's existing semantics in the model's vocabulary is what makes the comparison
    meaningful rather than tautological.
    """
    return tuple(
        StepTemplate(
            step_id=step.step_id,
            clauses=tuple(
                DependencyClause(
                    upstream_step=need,
                    mapping=ClauseMapping.COLLECT_ALL,
                    requirement=Requirement.SUCCEEDED,
                    cardinality=Cardinality.ALL,
                )
                for need in step.needs
            ),
        )
        for step in steps
    )


def _resolved_steps_from_spec(spec_path: Path) -> list[ResolvedStep]:
    """Resolve a real spec through the engine's own planner."""
    from metaproc.commands.helpers import load_process_spec  # noqa: PLC0415 -- heavy import
    from metaproc.engine.build_plan import build_plan  # noqa: PLC0415 -- heavy import
    from metaproc.engine.process_scope import expand_process_vars  # noqa: PLC0415 -- heavy import

    spec = load_process_spec(spec_path)
    params = expand_process_vars(
        spec,
        {"RUNS_DIR": "/tmp/equivalence", "RUN_ID": "equivalence"},
        process_dir=spec_path.parent,
    )
    plan = build_plan(spec, params, process_path=spec_path, validate_required_inputs=False)
    return list(plan.steps)


def _dispatch_order(state: RunState) -> list[list[str]]:
    """Drive the model to completion, recording what it dispatches in each wave.

    Each wave is one round of "everything the model says is ready now", which is the
    model's analogue of a topological level.
    """
    waves: list[list[str]] = []
    while True:
        state, commands = reduce(state, Tick())
        ready = sorted(c.task_key.step_id for c in commands if isinstance(c, DispatchAttempt))
        if not ready:
            return waves
        waves.append(ready)
        for step_id in ready:
            key = TaskKey(step_id)
            attempt = f"a-{step_id}"
            state, _ = reduce(
                state,
                AttemptStarted(attempt_id=attempt, task_key=key, generation=1, fence_epoch=0),
            )
            state, _ = reduce(
                state,
                AttemptEnded(attempt_id=attempt, disposition=AttemptDisposition.SUCCEEDED),
            )


def _spec_paths() -> list[Path]:
    return sorted(EXAMPLES.glob("*/*.process.md"))


class TestModelReproducesTheEngineLevelWalk:
    def test_at_least_one_real_spec_is_available(self) -> None:
        """Guard against the suite silently passing because it found no specs."""
        assert _spec_paths(), f"no example specs found under {EXAMPLES}"

    @pytest.mark.parametrize("spec_path", _spec_paths(), ids=lambda p: p.parent.name)
    def test_dispatch_waves_match_topological_levels(self, spec_path: Path) -> None:
        """The model's ready-set waves equal the engine's levels, step for step.

        This is the degenerate-equivalence claim: with step-scoped edges, task-level
        scheduling must produce exactly the schedule the level walk produces.
        """
        steps = _resolved_steps_from_spec(spec_path)
        engine_levels = [sorted(level) for level in topo_sort(steps)]

        model_waves = _dispatch_order(RunState(templates=_templates_from(steps)))

        assert model_waves == engine_levels

    @pytest.mark.parametrize("spec_path", _spec_paths(), ids=lambda p: p.parent.name)
    def test_every_step_reaches_a_terminal_success(self, spec_path: Path) -> None:
        """A spec whose steps all succeed must drain completely: no step left waiting,
        which is what would happen if a translated clause were unsatisfiable."""
        steps = _resolved_steps_from_spec(spec_path)
        state = RunState(templates=_templates_from(steps))

        for wave in _dispatch_order(state):
            for step_id in wave:
                key = TaskKey(step_id)
                attempt = f"a-{step_id}"
                state, _ = reduce(
                    state,
                    AttemptStarted(attempt_id=attempt, task_key=key, generation=1, fence_epoch=0),
                )
                state, _ = reduce(
                    state,
                    AttemptEnded(attempt_id=attempt, disposition=AttemptDisposition.SUCCEEDED),
                )

        assert [s.step_id for s in steps]
        for step in steps:
            assert task_state(state, TaskKey(step.step_id)) is TaskState.SUCCEEDED

    @pytest.mark.parametrize("spec_path", _spec_paths(), ids=lambda p: p.parent.name)
    def test_a_failed_step_blocks_exactly_the_engine_downstream_set(self, spec_path: Path) -> None:
        """Failure propagation must match the engine's `downstream` closure.

        The engine marks a failed step's transitive dependents blocked; the model settles
        them to skipped. Different words, and they must name the same set of steps.
        """
        from metaproc.engine.graph import downstream  # noqa: PLC0415 -- engine compare

        steps = _resolved_steps_from_spec(spec_path)
        roots = [s.step_id for s in steps if not s.needs]
        if not roots or len(steps) < 2:
            pytest.skip("spec has no root step with dependents to compare")
        failed = roots[0]

        state = RunState(templates=_templates_from(steps))
        attempt = f"a-{failed}"
        state, _ = reduce(
            state,
            AttemptStarted(
                attempt_id=attempt, task_key=TaskKey(failed), generation=1, fence_epoch=0
            ),
        )
        state, _ = reduce(
            state,
            AttemptEnded(attempt_id=attempt, disposition=AttemptDisposition.PERMANENT),
        )

        engine_blocked = set(downstream(steps, failed))
        model_skipped = {
            s.step_id for s in steps if task_state(state, TaskKey(s.step_id)) is TaskState.SKIPPED
        }

        assert model_skipped == engine_blocked
