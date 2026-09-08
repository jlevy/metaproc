"""Behavioral coverage for resuming an item-aligned chain."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.io.state_io import read_status_at
from metaproc.paths import STATE_DIR, STATUS_FILE, TASKS_SUBDIR

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "replay_smoke"
_PROCESS_PATH = _FIXTURE_DIR / "replay-smoke.process.md"
_RUN_ID = "aligned-chain-resume"
_ITEM = "alfa"


def _task_state_dir(run_dir: Path, step_id: str) -> Path:
    return run_dir / STATE_DIR / TASKS_SUBDIR / step_id / _ITEM


def test_resume_runs_an_incomplete_member_behind_a_complete_chain_head(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / _RUN_ID
    args = [
        "run-process",
        str(_PROCESS_PATH),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        f"RUN_ID={_RUN_ID}",
    ]
    runner = CliRunner()

    # Two other fixture items fail by design. This test follows `alfa`, which completes
    # all three stages, and uses its invocation log to distinguish reuse from execution.
    runner.invoke(app, args)

    process_status = yaml.safe_load((run_dir / STATE_DIR / "process-status.yaml").read_text())
    assert process_status["steps"]["stage-a"]["state"] == "completed"

    target_state_dir = _task_state_dir(run_dir, "stage-b")
    target_status = target_state_dir / STATUS_FILE
    target_output = run_dir / "items" / _ITEM / "stage-b.json"
    invocation_log = run_dir / "items" / _ITEM / "invocations.log"
    invocations_before_resume = invocation_log.read_text().splitlines()
    assert invocations_before_resume == ["stage-a", "stage-b", "stage-c"]

    target_status.unlink()
    target_output.unlink()

    runner.invoke(app, args)

    restored = read_status_at(target_state_dir)
    assert restored is not None
    assert restored.state == "completed"
    assert json.loads(target_output.read_text()) == {"item": _ITEM, "stage": "stage-b"}
    assert invocation_log.read_text().splitlines() == [
        *invocations_before_resume,
        "stage-b",
    ]
