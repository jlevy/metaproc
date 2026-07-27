"""Regression: run-process refuses multi-lane plans on real launches.

Regression coverage: review P1. The lane planner can materialize
``plan.execution_lanes`` with more than one row, but the runner does
not yet drive multiple lanes in a single pool — RunPoolConfig still
carries one ``execution_profile`` and ``run_parallel`` stamps that
single profile as the lane id on every submission. If a real launch
went through, the operator would get the dry-run preview's
``items × lanes`` count but only ``items × 1`` worth of actual work,
silently under the run profile. The guard refuses the launch and
points operators at the sibling-run shape until same-runpool
multi-lane drive lands.

Dry-run remains permissive so plan inspection still works.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app

_FIXTURE_BODY = """\
---
process:
  name: multilane-smoke
  description: Two-lane fixture for the multi-lane execution guard.
  lane_matrix:
    lanes:
      - execution_profile: codex-gpt55
        comparison_role: primary
      - execution_profile: claude-opus
        comparison_role: comparison

  defaults:
    default_execution_profile: codex-gpt55

  steps:
    - id: scaffold
      mode: code
      handler: noop.py:noop
---
"""


@pytest.fixture
def process_path(tmp_path: Path) -> Path:
    """Write the two-lane fixture and a no-op handler."""
    p = tmp_path / "multilane-smoke.process.md"
    p.write_text(_FIXTURE_BODY)
    handler = tmp_path / "noop.py"
    handler.write_text("def noop(ctx, step):\n    pass\n")
    return p


def test_dry_run_permits_multilane_preview(tmp_path: Path, process_path: Path) -> None:
    """--dry-run must still render the lanes × items projection."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=multilane-dry",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, (
        f"--dry-run multi-lane failed (exit={result.exit_code})\n{result.stdout}"
    )
    assert "Execution lanes: 2" in result.stdout
    assert "codex-gpt55" in result.stdout
    assert "claude-opus" in result.stdout


def test_real_launch_refused_for_multilane_plan(tmp_path: Path, process_path: Path) -> None:
    """Without --dry-run the runner must refuse with a clear pointer."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=multilane-real",
        ],
    )
    assert result.exit_code != 0
    err = result.stdout + (str(result.exception) if result.exception else "")
    assert "same-runpool multi-lane execution is not implemented" in err
    assert "--execution-profiles" in err  # operator pointer to dispatch_control


def test_single_lane_launch_still_runs(tmp_path: Path) -> None:
    """Degenerate single-lane plans (no lane_matrix) launch normally."""
    process_path = tmp_path / "single-lane.process.md"
    process_path.write_text(
        """\
---
process:
  name: single-lane-smoke
  defaults:
    default_execution_profile: codex-gpt55
  steps:
    - id: scaffold
      mode: code
      handler: noop.py:noop
---
"""
    )
    handler = tmp_path / "noop.py"
    handler.write_text("def noop(ctx, step):\n    pass\n")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=single-lane",
        ],
    )
    # We don't care about the eventual exit code (the no-op handler may or
    # may not succeed); we just need to confirm the multi-lane guard does
    # NOT fire on a single-lane plan.
    assert "same-runpool multi-lane execution is not implemented" not in result.stdout
