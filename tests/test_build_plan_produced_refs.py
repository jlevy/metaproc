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
    """A step that reads and rewrites one path keeps it in its own fingerprint.

    The fixture's producer lists its own declared output in `prompt_paths`, so the
    self-exclusion branch is consulted. Without that the assertion passes whether or not
    the branch exists, which is what the first version of this test did.
    """
    assert _step(_plan(tmp_path), "stage-source-snapshot").produced_refs == []


def test_the_match_is_keyed_not_string_compared(tmp_path: Path) -> None:
    """A doubled slash on one side must not decide whether a file is produced.

    `stage-oddly-spelled` declares `{{run.dir}}//segment-note.md` and the reader spells it
    plainly. Same file. Comparing the raw strings makes it unproduced, which reinstates the
    plan-time FileNotFoundError this whole change exists to remove, and does so only for
    spellings `normalize_path_key` exists to absorb.
    """
    produced = _step(_plan(tmp_path), "decompose").produced_refs
    assert any(ref.endswith("segment-note.md") for ref in produced)
