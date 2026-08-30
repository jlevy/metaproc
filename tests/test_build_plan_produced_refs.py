"""`produced_refs` when a raw path names a file another step writes.

A step that references `{{run.dir}}/x.md` where an earlier step declares that exact path
as an output is reading execution state, not an authored input. Its bytes must leave the
fingerprint, or the plan cannot be published before the run produces the file.
"""

from __future__ import annotations

from pathlib import Path

from metaproc.commands.helpers import load_process_spec
from metaproc.engine.build_plan import build_plan

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/produced_raw_path/staged-then-read.process.md"
)


def _plan(tmp_path: Path):
    spec = load_process_spec(FIXTURE)
    return build_plan(spec, {"run.dir": str(tmp_path)}, process_path=FIXTURE)


def _step(plan, step_id: str):
    return next(step for step in plan.steps if step.step_id == step_id)


def test_a_raw_path_another_step_writes_is_produced(tmp_path: Path) -> None:
    produced = _step(_plan(tmp_path), "decompose").produced_refs
    assert str(tmp_path / "company-profile.md") in produced


def test_an_authored_input_is_not_produced(tmp_path: Path) -> None:
    """Nothing in the plan writes it, so its bytes stay in the fingerprint and a missing
    file remains the misconfiguration it has always been."""
    produced = _step(_plan(tmp_path), "decompose").produced_refs
    assert str(tmp_path / "authored-input.md") not in produced


def test_a_producer_does_not_exclude_its_own_output(tmp_path: Path) -> None:
    """A step's own outputs are not inputs to itself; excluding them would let a step that
    reads and rewrites one path drop it from its fingerprint."""
    assert _step(_plan(tmp_path), "stage-source-snapshot").produced_refs == []
