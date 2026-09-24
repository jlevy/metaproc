"""A step's fingerprint does not depend on where its checkout sits.

A plan resolves ``./`` and ``../`` references against the process file, so a composite's
``uses_path``, a step's ``prompt_paths``, and any path a resolved field carries are
absolute. ``fingerprint_step`` hashes the paths inside the step's checkout relative to
it, so the same revision planned from two checkouts gives every step one fingerprint and
a resume from a fresh checkout reuses what the run completed. The bytes a step reads are
still hashed, so an edit still re-runs it.

A checkout here is a directory holding ``.git``, which is what ``build_plan`` looks for.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

from typer.testing import CliRunner, Result

from metaproc.cli import app
from metaproc.commands.helpers import load_process_spec
from metaproc.engine.build_plan import build_plan
from metaproc.engine.dep_state import fingerprint_step
from metaproc.engine.process_scope import expand_process_vars
from metaproc.models.authored import IOSpec
from metaproc.models.plan import ResolvedAdapter, ResolvedStep

_HANDLERS = '''\
"""Code handlers that log each invocation into the run directory."""

from __future__ import annotations

from pathlib import Path


def _log(variables: dict[str, str], name: str) -> None:
    run_dir = Path(variables["run.dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    root = Path(variables["RUNS_DIR"]) / variables["RUN_ID"].split("/")[0]
    with (root / "invocations.log").open("a", encoding="utf-8") as fh:
        fh.write(name + "\\n")
    (run_dir / f"{name}.txt").write_text("done\\n", encoding="utf-8")


def stage(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    _log(variables, "stage")


def leaf(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    _log(variables, "leaf")
'''


def _write_checkout(root: Path, *, with_git: bool = True) -> Path:
    """Write a parent process whose composite runs a child that loads a runbook.

    The child's ``stage`` step reads a data file inside the checkout through its
    ``inputs:`` and references a runbook through ``prompt_paths``; ``leaf`` runs after
    it. Both are code steps that log each run, so a test can count what re-ran.
    """
    process_dir = root / "flows"
    process_dir.mkdir(parents=True, exist_ok=True)
    if with_git:
        (root / ".git").mkdir(exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "roster.txt").write_text("alpha\n", encoding="utf-8")
    (process_dir / "handlers.py").write_text(_HANDLERS, encoding="utf-8")
    (process_dir / "guide.runbook.md").write_text("# Guide\n\nRead the roster.\n")
    (process_dir / "child.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: child
              inputs:
                roster: {param: ROSTER, as: path}
              deps:
                guide: {path: ./guide.runbook.md, as: path}
              steps:
                - id: stage
                  mode: code
                  handler: "handlers.py:stage"
                  prompt_paths: [deps.guide]
                  inputs:
                    roster: {path: "{{roster}}", kind: file}
                  outputs:
                    out: {path: "{{run.dir}}/stage.txt", kind: file}
                - id: leaf
                  mode: code
                  handler: "handlers.py:leaf"
                  needs: [stage]
                  outputs:
                    out: {path: "{{run.dir}}/leaf.txt", kind: file}
            ---
            # Child
            """
        ),
        encoding="utf-8",
    )
    parent = process_dir / "parent.process.md"
    parent.write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: parent
              deps:
                child: {path: ./child.process.md, as: path}
              steps:
                - id: nested
                  mode: composite
                  uses: deps.child
                  with:
                    roster: "{{ROSTER}}"
            ---
            # Parent
            """
        ),
        encoding="utf-8",
    )
    return parent


def _fingerprints(process_path: Path, variables: dict[str, str]) -> dict[str, str]:
    spec = load_process_spec(process_path)
    params = expand_process_vars(spec, dict(variables))
    plan = build_plan(spec, params, process_path=process_path, validate_required_inputs=False)
    return {step.step_id: fingerprint_step(step) for step in plan.steps}


def _child_fingerprints(checkout: Path, runs_dir: Path) -> dict[str, str]:
    return _fingerprints(
        checkout / "flows" / "child.process.md",
        {
            "ROSTER": str(checkout / "data" / "roster.txt"),
            "RUN_ID": "run-1",
            "RUNS_DIR": str(runs_dir),
        },
    )


def test_two_checkouts_of_one_revision_give_every_step_one_fingerprint(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    first = _write_checkout(tmp_path / "first")
    second = _write_checkout(tmp_path / "second")

    plan_variables = {"RUN_ID": "run-1", "RUNS_DIR": str(runs_dir)}
    assert _fingerprints(first, plan_variables) == _fingerprints(second, plan_variables)
    assert _child_fingerprints(tmp_path / "first", runs_dir) == _child_fingerprints(
        tmp_path / "second", runs_dir
    )


def test_an_edit_in_the_second_checkout_still_changes_the_fingerprint(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_checkout(tmp_path / "first")
    second = _write_checkout(tmp_path / "second")
    (second.parent / "guide.runbook.md").write_text("# Guide\n\nRead it twice.\n")

    first_child = _child_fingerprints(tmp_path / "first", runs_dir)
    second_child = _child_fingerprints(tmp_path / "second", runs_dir)

    assert first_child["stage"] != second_child["stage"]
    assert first_child["leaf"] == second_child["leaf"]


def test_a_path_outside_the_checkout_is_still_hashed_as_it_is(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    _write_checkout(checkout)
    elsewhere = tmp_path / "elsewhere"
    for name in ("a", "b"):
        (elsewhere / name).mkdir(parents=True)
        (elsewhere / name / "roster.txt").write_text("alpha\n", encoding="utf-8")

    def stage(roster: Path) -> str:
        return _fingerprints(
            checkout / "flows" / "child.process.md",
            {"ROSTER": str(roster), "RUN_ID": "run-1", "RUNS_DIR": str(tmp_path / "runs")},
        )["stage"]

    assert stage(elsewhere / "a" / "roster.txt") != stage(elsewhere / "b" / "roster.txt")


def _step(checkout_root: Path | None, roster: Path) -> ResolvedStep:
    return ResolvedStep(
        step_id="s1",
        mode="code",
        adapter=ResolvedAdapter(type="test", config={}),
        inputs={"roster": IOSpec(path=str(roster))},
        env={"TOOL": f"{roster.parent}/tool --root {roster.parent}"},
        checkout_root=str(checkout_root) if checkout_root is not None else None,
    )


def test_a_sibling_whose_name_extends_the_root_is_not_rewritten(tmp_path: Path) -> None:
    """``/work/repo`` must not match inside ``/work/repo-2``: that is another directory."""
    root = tmp_path / "repo"
    inside = _step(root, root / "roster.txt")
    sibling = _step(root, tmp_path / "repo-2" / "roster.txt")
    moved = _step(tmp_path / "moved", tmp_path / "moved" / "roster.txt")

    assert fingerprint_step(inside) == fingerprint_step(moved)
    assert fingerprint_step(sibling) != fingerprint_step(inside)


def test_the_checkout_root_itself_is_not_in_the_hash(tmp_path: Path) -> None:
    roster = tmp_path / "shared" / "roster.txt"

    assert fingerprint_step(_step(tmp_path / "one", roster)) == fingerprint_step(
        _step(tmp_path / "two", roster)
    )


def test_a_process_outside_any_checkout_hashes_its_paths_as_they_are(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    first = _write_checkout(tmp_path / "first", with_git=False)
    second = _write_checkout(tmp_path / "second", with_git=False)
    plan_variables = {"RUN_ID": "run-1", "RUNS_DIR": str(runs_dir)}

    spec = load_process_spec(first)
    plan = build_plan(spec, expand_process_vars(spec, dict(plan_variables)), process_path=first)
    assert all(step.checkout_root is None for step in plan.steps)
    assert _fingerprints(first, plan_variables) != _fingerprints(second, plan_variables)


def test_build_plan_records_the_checkout_holding_the_process(tmp_path: Path) -> None:
    parent = _write_checkout(tmp_path / "checkout")
    spec = load_process_spec(parent)
    plan = build_plan(
        spec,
        expand_process_vars(spec, {"RUN_ID": "run-1", "RUNS_DIR": str(tmp_path / "runs")}),
        process_path=parent,
    )

    assert [step.checkout_root for step in plan.steps] == [str((tmp_path / "checkout").resolve())]


# ── End to end through run-process ────────────────────────────────


def _run(process_path: Path, runs_dir: Path, run_id: str, roster: Path) -> Result:
    return CliRunner().invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            f"RUN_ID={run_id}",
            "--var",
            f"ROSTER={roster}",
            "--backend",
            "local",
        ],
    )


def _invocations(run_dir: Path) -> list[str]:
    return (run_dir / "invocations.log").read_text(encoding="utf-8").splitlines()


def test_a_resume_from_a_second_checkout_reuses_everything_the_run_completed(
    tmp_path: Path,
) -> None:
    """The run launches from one checkout and resumes from a copy of it at another path.

    The resume names the roster, a file inside the checkout, in the checkout it runs
    from, as a relaunch there would. Nothing re-runs: no fingerprint moved, so the
    composite is re-entered and neither child step is invoked again. An edit to the
    child's runbook in the second checkout is still seen by the next resume, which
    re-runs the step that references it and the step after it.
    """
    runs_dir = tmp_path / "runs"
    run_id = "checkout-run"
    run_dir = runs_dir / run_id
    first = _write_checkout(tmp_path / "first")
    shutil.copytree(tmp_path / "first", tmp_path / "second")
    second = tmp_path / "second" / "flows" / "parent.process.md"
    launched = _run(first, runs_dir, run_id, tmp_path / "first" / "data" / "roster.txt")
    assert launched.exit_code == 0, launched.output
    assert _invocations(run_dir) == ["stage", "leaf"]

    roster = tmp_path / "second" / "data" / "roster.txt"
    resumed = _run(second, runs_dir, run_id, roster)
    assert resumed.exit_code == 0, resumed.output
    assert "fingerprint changed" not in resumed.output
    assert _invocations(run_dir) == ["stage", "leaf"]

    guide = tmp_path / "second" / "flows" / "guide.runbook.md"
    guide.write_text("# Guide\n\nRead it twice.\n", encoding="utf-8")
    edited = _run(second, runs_dir, run_id, roster)
    assert edited.exit_code == 0, edited.output
    assert "Step 'stage': fingerprint changed" in edited.output
    assert _invocations(run_dir) == ["stage", "leaf", "stage", "leaf"]
