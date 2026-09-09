"""Behavioral coverage for resuming a standalone code fan-out step.

The item-aligned chain executor decides reuse per item per step through its `is_done`
guard. A `mode: code` step with `for_each` that is not part of a chain goes through a
different executor, and that one had no such guard: discovery hands it every
non-terminal item, completed ones included, and every row is invoked.

`tests/test_item_aligned_chain_resume.py` covers the chain path. This file covers the
standalone one, which the repository's only other code fan-out fixture cannot reach
because its steps declare `align: same_key`.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.paths import STATE_DIR

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "code_fanout_resume"
_PROCESS_PATH = _FIXTURE_DIR / "code-fanout-resume.process.md"
_RUN_ID = "code-fanout-resume"
_ITEMS = ["alfa", "brvo", "chrl"]


def _args(runs_dir: Path) -> list[str]:
    return [
        "run-process",
        str(_PROCESS_PATH),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        f"RUN_ID={_RUN_ID}",
    ]


def test_bare_resume_reuses_completed_code_fan_out_items(tmp_path: Path) -> None:
    """A completed item is not invoked a second time by a bare same-RUN_ID resume."""
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / _RUN_ID
    runner = CliRunner()

    first = runner.invoke(app, _args(runs_dir))
    assert first.exit_code == 0, first.output

    process_status = yaml.safe_load((run_dir / STATE_DIR / "process-status.yaml").read_text())
    assert process_status["steps"]["project"]["state"] == "completed"

    invocation_log = run_dir / "items" / "invocations.log"
    after_first = sorted(invocation_log.read_text().splitlines())
    assert after_first == _ITEMS

    # Bare resume: same RUN_ID, no --force, no step selector, nothing removed. Every
    # item's declared output is present and its task state says completed, so there is
    # no work left to do and the handler should not run again.
    second = runner.invoke(app, _args(runs_dir))
    assert second.exit_code == 0, second.output

    after_resume = sorted(invocation_log.read_text().splitlines())
    assert after_resume == _ITEMS, (
        "resume re-invoked completed items; the handler ran again for "
        f"{[item for item in after_resume if after_resume.count(item) > 1]}"
    )


def test_resume_reruns_an_item_whose_output_went_missing(tmp_path: Path) -> None:
    """Reuse is earned by a valid output, not merely by a completed status record."""
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / _RUN_ID
    runner = CliRunner()

    first = runner.invoke(app, _args(runs_dir))
    assert first.exit_code == 0, first.output

    invocation_log = run_dir / "items" / "invocations.log"
    assert sorted(invocation_log.read_text().splitlines()) == _ITEMS

    (run_dir / "items" / "brvo" / "project.json").unlink()

    second = runner.invoke(app, _args(runs_dir))
    assert second.exit_code == 0, second.output

    after_resume = sorted(invocation_log.read_text().splitlines())
    assert after_resume == sorted([*_ITEMS, "brvo"]), (
        "resume should rerun exactly the item whose declared output is gone"
    )
