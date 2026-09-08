"""The reference model checked against a run the engine actually executed.

``test_engine_equivalence`` ties the model to the engine for step-scoped edges by
comparing schedules on paper. This suite closes the item-scoped half of the gap: it runs
a fixture process through the real engine (aligned chain, declared retry with
``on_invalid``, an operational failure, a contract failure, a ``require: finished``
fan-in), replays the run's durable facts through the reducer, and asserts the model
lands on the same terminal state for every task the engine touched, including the ones
it deliberately never ran.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.execution_model.model import RunState, TaskKey, TaskState
from metaproc.execution_model.reducer import task_state
from metaproc.execution_model.trace import engine_task_facts, replay_run
from metaproc.io import read_yaml_file, to_yaml_string
from metaproc.io.state_io import read_attempt_history_at, read_status_at, start_attempt_at
from metaproc.models.plan import ResolvedStep
from metaproc.models.runtime import AttemptDisposition
from metaproc.paths import ATTEMPT_FILE, attempt_state_dir, task_state_dir

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "replay_smoke"
_ROSTER = ("alfa", "brvo", "chrl", "dlta", "echo")
_SURVIVORS = ("alfa", "brvo", "chrl")


def _resolved_steps(runs_dir: Path, run_id: str) -> list[ResolvedStep]:
    from metaproc.commands.helpers import load_process_spec  # noqa: PLC0415 -- heavy import
    from metaproc.engine.build_plan import build_plan  # noqa: PLC0415 -- heavy import
    from metaproc.engine.process_scope import expand_process_vars  # noqa: PLC0415 -- heavy import

    spec_path = _FIXTURE_DIR / "replay-smoke.process.md"
    spec = load_process_spec(spec_path)
    params = expand_process_vars(
        spec,
        {"RUNS_DIR": str(runs_dir), "RUN_ID": run_id},
        process_dir=spec_path.parent,
    )
    plan = build_plan(spec, params, process_path=spec_path, validate_required_inputs=False)
    return list(plan.steps)


@pytest.fixture(scope="module")
def smoke() -> Iterator[tuple[Path, list[ResolvedStep]]]:
    """Run the fixture once through the engine; yield the run dir and its plan."""
    with tempfile.TemporaryDirectory(prefix="replay-smoke-") as tmp:
        runs_dir = Path(tmp) / "runs"
        run_id = "replay-smoke-run"
        result = CliRunner().invoke(
            app,
            [
                "run-process",
                str(_FIXTURE_DIR / "replay-smoke.process.md"),
                "--var",
                f"RUNS_DIR={runs_dir}",
                "--var",
                f"RUN_ID={run_id}",
            ],
        )
        # Two items fail by design, so the run itself reports failure; the tree is
        # what the replay consumes.
        run_dir = runs_dir / run_id
        assert run_dir.is_dir(), (
            f"run tree missing (exit={result.exit_code})\n{result.stdout}\n{result.exception}"
        )
        yield run_dir, _resolved_steps(runs_dir, run_id)


@pytest.fixture(scope="module")
def replayed(smoke: tuple[Path, list[ResolvedStep]]) -> RunState:
    run_dir, steps = smoke
    return replay_run(run_dir, steps)


class TestReplayMatchesEngine:
    def test_every_recorded_task_lands_on_the_same_terminal_state(
        self, smoke: tuple[Path, list[ResolvedStep]], replayed: RunState
    ) -> None:
        """The generic diff: engine record against model projection, task by task."""
        run_dir, steps = smoke
        facts = engine_task_facts(run_dir, steps)
        assert facts, "the engine recorded nothing; the fixture run did not happen"
        for key, fact in sorted(facts.items(), key=lambda kv: str(kv[0])):
            expected = (
                TaskState.SUCCEEDED if fact.state in {"completed", "cached"} else TaskState.FAILED
            )
            assert task_state(replayed, key) is expected, (
                f"{key}: engine recorded {fact.state!r}, model projects "
                f"{task_state(replayed, key).value!r}"
            )

    def test_attempt_counts_survive_the_replay(
        self, smoke: tuple[Path, list[ResolvedStep]], replayed: RunState
    ) -> None:
        """Replay fidelity is to the durable record, attempt for attempt."""
        run_dir, steps = smoke
        for key, fact in engine_task_facts(run_dir, steps).items():
            modeled = sum(1 for a in replayed.attempts if a.task_key == key)
            assert modeled == fact.attempts, (
                f"{key}: engine recorded {fact.attempts} attempts, replay holds {modeled}"
            )

    def test_durable_attempt_history_records_every_retry(
        self, smoke: tuple[Path, list[ResolvedStep]], replayed: RunState
    ) -> None:
        """The silent item was launched three times and every attempt is replayable."""
        run_dir, steps = smoke
        invocations = (run_dir / "items" / "echo" / "invocations.log").read_text()
        assert invocations.count("stage-b") == 3
        key = TaskKey("stage-b", "echo")
        assert engine_task_facts(run_dir, steps)[key].attempts == 3
        assert sum(1 for attempt in replayed.attempts if attempt.task_key == key) == 3
        history = read_attempt_history_at(task_state_dir(run_dir, "stage-b", "echo"))
        assert [record.disposition for record in history] == [
            AttemptDisposition.retryable,
            AttemptDisposition.retryable,
            AttemptDisposition.retryable,
        ]
        assert task_state(replayed, key) is TaskState.FAILED

    def test_replay_rejects_attempt_from_a_different_run(
        self, smoke: tuple[Path, list[ResolvedStep]], tmp_path: Path
    ) -> None:
        run_dir, steps = smoke
        copied_run = tmp_path / run_dir.name
        shutil.copytree(run_dir, copied_run)
        state_dir = task_state_dir(copied_run, "stage-b", "echo")
        attempt = read_attempt_history_at(state_dir)[0]
        attempt_path = attempt_state_dir(state_dir, attempt.attempt_id) / ATTEMPT_FILE
        payload = read_yaml_file(attempt_path)
        payload["run_id"] = "different-run"
        attempt_path.write_text(to_yaml_string(payload))

        with pytest.raises(ValueError, match="different-run"):
            replay_run(copied_run, steps)

    def test_replay_rejects_status_whose_current_attempt_is_missing(
        self, smoke: tuple[Path, list[ResolvedStep]], tmp_path: Path
    ) -> None:
        run_dir, steps = smoke
        copied_run = tmp_path / run_dir.name
        shutil.copytree(run_dir, copied_run)
        state_dir = task_state_dir(copied_run, "stage-b", "echo")
        status = read_status_at(state_dir)
        assert status is not None
        assert status.attempt_id is not None
        (attempt_state_dir(state_dir, status.attempt_id) / ATTEMPT_FILE).unlink()

        with pytest.raises(ValueError, match="missing attempt"):
            replay_run(copied_run, steps)

    def test_replay_rejects_status_that_does_not_name_latest_attempt(
        self, smoke: tuple[Path, list[ResolvedStep]], tmp_path: Path
    ) -> None:
        run_dir, steps = smoke
        copied_run = tmp_path / run_dir.name
        shutil.copytree(run_dir, copied_run)
        state_dir = task_state_dir(copied_run, "stage-b", "echo")
        status = read_status_at(state_dir)
        assert status is not None
        orphan = start_attempt_at(
            state_dir,
            run_id=status.run_id,
            step_id=status.step_id,
            item=status.item,
            item_key="echo",
        )

        with pytest.raises(ValueError, match=f"latest attempt {orphan.attempt_id!r}"):
            replay_run(copied_run, steps)

    def test_the_operational_failure_stopped_at_one_attempt(
        self, smoke: tuple[Path, list[ResolvedStep]], replayed: RunState
    ) -> None:
        """dlta raised; the engine did not retry it, and neither may the replay."""
        run_dir, steps = smoke
        key = TaskKey("stage-b", "dlta")
        assert engine_task_facts(run_dir, steps)[key].attempts == 1
        assert task_state(replayed, key) is TaskState.FAILED

    def test_items_the_engine_never_ran_project_as_skipped(self, replayed: RunState) -> None:
        """dlta and echo have no stage-c record; the model must say why, not lose them.

        The engine expresses the skip as an absence. The model, given the same facts,
        must land those tasks in SKIPPED via the dead aligned clause, which is the
        propagation equivalence the level walk never had to state explicitly.
        """
        for item in ("dlta", "echo"):
            assert task_state(replayed, TaskKey("stage-c", item)) is TaskState.SKIPPED

    def test_the_chain_did_not_over_skip(self, replayed: RunState) -> None:
        for item in _SURVIVORS:
            for step in ("stage-a", "stage-b", "stage-c"):
                assert task_state(replayed, TaskKey(step, item)) is TaskState.SUCCEEDED

    def test_review_succeeds_over_a_partly_failed_roster(
        self, smoke: tuple[Path, list[ResolvedStep]], replayed: RunState
    ) -> None:
        """`require: finished` accepted the failures in both implementations.

        The engine ran the review because `_requires_only_finished` tolerates upstream
        failure on a collected edge; the model must reach the same conclusion from the
        FINISHED requirement over a roster holding successes, failures, and skips.
        """
        run_dir, steps = smoke
        key = TaskKey("review")
        assert engine_task_facts(run_dir, steps)[key].state == "completed"
        assert task_state(replayed, key) is TaskState.SUCCEEDED
