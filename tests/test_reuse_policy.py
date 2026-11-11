"""``reuse_policy`` decides whether a completed step is reused on a later run.

Spec: ``src/metaproc/docs/metaproc-design.md`` §10.2 and §10.3.

The authored values are ``validated_outputs`` (the default), ``exact_inputs`` and
``never``; any other value is rejected when the spec loads.

- ``never`` re-runs a scalar step on every walk that reaches it. Its downstream steps
  are judged by their own policy. A mapped step cannot declare it.
- ``exact_inputs`` is ``validated_outputs`` plus content coverage: the step fingerprint
  also hashes the handler's source file and every authored file input the run does not
  itself produce, so editing either re-runs the step and its downstream.
- A step that does not opt in keeps the fingerprint it had before ``exact_inputs``
  existed, so no resumable run re-executes on upgrade. The pinned values below are the
  fingerprints the unchanged code computed for these exact fixtures.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner, Result

from metaproc.cli import app
from metaproc.commands.helpers import load_process_spec
from metaproc.engine import dep_state
from metaproc.engine.build_plan import build_plan
from metaproc.engine.dep_state import fingerprint_step
from metaproc.engine.process_scope import expand_process_vars
from metaproc.io.state_io import read_result_at, read_run_plan
from metaproc.models.authored import IOSpec, ProcessDefaults, ProcessStep
from metaproc.models.plan import FanOut, Plan, ResolvedAdapter, ResolvedStep
from metaproc.paths import STATE_DIR, TASKS_SUBDIR

_ROSTER = (
    "---\nprogress:\n  schema: metaproc:ProgressSpec/0.1\n  process: mapped\n"
    "  items:\n    - ticker: AAPL\n---\n# Roster\n"
)


def _handler(name: str) -> str:
    """Source of a code handler that logs its invocation and writes its output."""
    return textwrap.dedent(
        f'''\
        """Handler for the {name} step."""

        from __future__ import annotations

        from pathlib import Path


        def run(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
            run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
            run_dir.mkdir(parents=True, exist_ok=True)
            with (run_dir / "invocations.log").open("a", encoding="utf-8") as fh:
                fh.write("{name}\\n")
            (run_dir / "{name}.txt").write_text("{name} done\\n", encoding="utf-8")
        '''
    )


def _code_step(
    step_id: str,
    *,
    reuse_policy: str | None = None,
    needs: tuple[str, ...] = (),
    inputs: str = "",
) -> str:
    lines = [
        f"    - id: {step_id}",
        "      mode: code",
        f'      handler: "{step_id}.py:run"',
    ]
    if reuse_policy is not None:
        lines.append(f"      reuse_policy: {reuse_policy}")
    if needs:
        lines.append(f"      needs: [{', '.join(needs)}]")
    if inputs:
        lines.append("      inputs:")
        lines.extend(f"        {line}" for line in inputs.strip().splitlines())
    lines.extend(
        [
            "      outputs:",
            f'        out: {{path: "{{{{run.dir}}}}/{step_id}.txt", kind: file}}',
        ]
    )
    return "\n".join(lines)


def _write_process(
    checkout: Path,
    name: str,
    steps: list[str],
    *,
    defaults: str = "",
    deps: str = "",
) -> Path:
    """Write a process of code steps, one handler file per step, inside a checkout."""
    flows = checkout / "flows"
    flows.mkdir(parents=True, exist_ok=True)
    (checkout / ".git").mkdir(exist_ok=True)
    header = [
        "---",
        "process:",
        f"  name: {name}",
    ]
    if defaults:
        header.append("  defaults:")
        header.extend(f"    {line}" for line in defaults.strip().splitlines())
    if deps:
        header.append("  deps:")
        header.extend(f"    {line}" for line in deps.strip().splitlines())
    header.append("  steps:")
    body = "\n".join([*header, *steps, "---", f"# {name}", ""])
    process_path = flows / f"{name}.process.md"
    process_path.write_text(body, encoding="utf-8")
    for step_text in steps:
        step_id = step_text.split("id: ", 1)[1].split("\n", 1)[0].strip()
        (flows / f"{step_id}.py").write_text(_handler(step_id), encoding="utf-8")
    return process_path


def _run(process_path: Path, runs_dir: Path, run_id: str) -> Result:
    return CliRunner().invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            f"RUN_ID={run_id}",
            "--backend",
            "local",
        ],
    )


def _invocations(run_dir: Path) -> list[str]:
    return (run_dir / "invocations.log").read_text(encoding="utf-8").splitlines()


def _plan(process_path: Path, variables: dict[str, str]) -> Plan:
    spec = load_process_spec(process_path)
    params = expand_process_vars(spec, dict(variables))
    return build_plan(spec, params, process_path=process_path, validate_required_inputs=False)


# ── never ─────────────────────────────────────────────────────────


def test_never_reruns_a_completed_scalar_step_and_reuses_its_neighbours(
    tmp_path: Path,
) -> None:
    process = _write_process(
        tmp_path / "checkout",
        "never-step",
        [
            _code_step("first"),
            _code_step("stamp", reuse_policy="never", needs=("first",)),
            _code_step("last", needs=("stamp",)),
        ],
    )
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "never-run"

    launched = _run(process, runs_dir, "never-run")
    assert launched.exit_code == 0, launched.output
    assert _invocations(run_dir) == ["first", "stamp", "last"]

    resumed = _run(process, runs_dir, "never-run")
    assert resumed.exit_code == 0, resumed.output
    assert _invocations(run_dir) == ["first", "stamp", "last", "stamp"]
    assert "Step 'first': already completed" in resumed.output
    assert "Step 'stamp': already completed" not in resumed.output
    assert "Step 'last': already completed" in resumed.output


def test_a_never_default_reruns_every_scalar_step(tmp_path: Path) -> None:
    process = _write_process(
        tmp_path / "checkout",
        "never-default",
        [_code_step("first"), _code_step("last", needs=("first",))],
        defaults="reuse_policy: never",
    )
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "never-default-run"

    assert _run(process, runs_dir, "never-default-run").exit_code == 0
    resumed = _run(process, runs_dir, "never-default-run")
    assert resumed.exit_code == 0, resumed.output
    assert _invocations(run_dir) == ["first", "last", "first", "last"]


def test_a_never_ancestor_left_out_of_the_walk_still_counts_as_completed(
    tmp_path: Path,
) -> None:
    """``--from`` omits a completed ``never`` ancestor; its completion still satisfies it."""
    process = _write_process(
        tmp_path / "checkout",
        "never-ancestor",
        [_code_step("stamp", reuse_policy="never"), _code_step("last", needs=("stamp",))],
    )
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "never-ancestor-run"
    assert _run(process, runs_dir, "never-ancestor-run").exit_code == 0

    narrowed = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            "RUN_ID=never-ancestor-run",
            "--from",
            "last",
            "--force",
            "--backend",
            "local",
        ],
    )
    assert narrowed.exit_code == 0, narrowed.output
    assert _invocations(run_dir) == ["stamp", "last", "last"]


def _mapped_spec(tmp_path: Path, *, step_policy: str | None, default_policy: str | None) -> Path:
    checkout = tmp_path / "checkout"
    flows = checkout / "flows"
    flows.mkdir(parents=True, exist_ok=True)
    (flows / "roster.md").write_text(_ROSTER, encoding="utf-8")
    (flows / "each.py").write_text(_handler("each"), encoding="utf-8")
    defaults = f"  defaults:\n    reuse_policy: {default_policy}\n" if default_policy else ""
    step_line = f"      reuse_policy: {step_policy}\n" if step_policy else ""
    process = flows / "mapped.process.md"
    process.write_text(
        "---\n"
        "process:\n"
        "  name: mapped\n"
        f"{defaults}"
        "  deps:\n"
        "    roster: {path: ./roster.md, as: path}\n"
        "  steps:\n"
        "    - id: each\n"
        "      mode: code\n"
        '      handler: "each.py:run"\n'
        f"{step_line}"
        "      for_each:\n"
        "        over: deps.roster\n"
        "        bind: ticker\n"
        "        bind_fields: [ticker]\n"
        '        key: "{{ticker}}"\n'
        "      outputs:\n"
        '        out: {path: "{{run.dir}}/{{ticker}}.txt", kind: file}\n'
        "---\n# Mapped\n",
        encoding="utf-8",
    )
    return process


@pytest.mark.parametrize(
    ("step_policy", "default_policy", "source"),
    [("never", None, "the step"), (None, "never", "defaults")],
)
def test_never_on_a_mapped_step_is_refused_at_planning(
    tmp_path: Path, step_policy: str | None, default_policy: str | None, source: str
) -> None:
    process = _mapped_spec(tmp_path, step_policy=step_policy, default_policy=default_policy)
    with pytest.raises(ValueError, match=r"step 'each'.*reuse_policy 'never'") as caught:
        _plan(process, {"RUN_ID": "r1", "RUNS_DIR": str(tmp_path / "runs")})
    assert f"from {source}" in str(caught.value)


def test_a_mapped_step_may_override_a_never_default(tmp_path: Path) -> None:
    process = _mapped_spec(tmp_path, step_policy="validated_outputs", default_policy="never")
    plan = _plan(process, {"RUN_ID": "r1", "RUNS_DIR": str(tmp_path / "runs")})
    assert [step.reuse_policy for step in plan.steps] == ["validated_outputs"]


# ── Accepted values ───────────────────────────────────────────────


@pytest.mark.parametrize("value", ["always_rerun", "trust_state", "validated-outputs"])
def test_an_undocumented_reuse_policy_is_rejected(value: str) -> None:
    step = {"id": "s1", "mode": "code", "handler": "h.py:run", "reuse_policy": value}
    with pytest.raises(ValidationError, match="validated_outputs"):
        ProcessStep.model_validate(step)
    with pytest.raises(ValidationError, match="validated_outputs"):
        ProcessDefaults.model_validate({"reuse_policy": value})


@pytest.mark.parametrize("value", ["validated_outputs", "exact_inputs", "never"])
def test_every_documented_reuse_policy_is_accepted(value: str) -> None:
    step = {"id": "s1", "mode": "code", "handler": "h.py:run", "reuse_policy": value}
    assert ProcessStep.model_validate(step).reuse_policy == value
    assert ProcessDefaults.model_validate({"reuse_policy": value}).reuse_policy == value


# ── Non-opted fingerprints are pinned ─────────────────────────────

# Computed by the code before ``exact_inputs`` hashed any content. A change here
# re-runs every completed step of that shape in every resumable run.
_PINNED_DIRECT = {
    "code-handler": "11203ca03bc2ee40",
    "code-command": "af46099f53cec111",
    "agent-runbook": "a52711de5cbf5ea6",
    "composite": "1b9b65337888f18f",
    "mapped": "369b921a5301d970",
}

_PINNED_PLANNED = {
    "load": "8499e62404d9f7b3",
    "score": "dd03e101c9974059",
    "report": "cda5eae2fdb5c4b8",
}


def _direct_steps(root: Path) -> dict[str, ResolvedStep]:
    """One resolved step per shape, with every path inside a fixed checkout root."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "runbook.md").write_text("# Runbook\n\nDo the thing.\n", encoding="utf-8")
    (root / "child.process.md").write_text("---\nprocess:\n  name: c\n---\n", encoding="utf-8")
    (root / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    common: dict[str, object] = {
        "adapter": ResolvedAdapter(type="claude-code-cli", config={"model": "pinned"}),
        "checkout_root": str(root),
        "reuse_policy": "validated_outputs",
    }
    data_input = {"data": IOSpec(path=str(root / "data.csv"), kind="file")}
    out = {"out": IOSpec(path=str(root / "runs" / "r1" / "out.txt"), kind="file")}
    shapes: dict[str, dict[str, object]] = {
        "code-handler": {
            "mode": "code",
            "handler": "handler.py:run",
            "inputs": data_input,
            "outputs": out,
        },
        "code-command": {
            "mode": "code",
            "command": "python tool.py --out {{run.dir}}",
            "inputs": data_input,
            "outputs": out,
            "env": {"TOOL_ROOT": str(root)},
        },
        "agent-runbook": {
            "mode": "agent",
            "prompt_paths": [str(root / "runbook.md")],
            "inputs": data_input,
            "outputs": out,
        },
        "composite": {
            "mode": "composite",
            "uses_path": str(root / "child.process.md"),
            "with_": {"roster": str(root / "data.csv")},
        },
        "mapped": {
            "mode": "code",
            "handler": "handler.py:run",
            "fan_out": FanOut(
                over="roster",
                bind="ticker",
                source=str(root / "data.csv"),
                bind_fields=["ticker"],
                items=[{"ticker": "AAPL"}],
                filtered_count=1,
            ),
            "inputs": {"roster": IOSpec(path=str(root / "data.csv"), kind="file")},
            "outputs": {"out": IOSpec(path=str(root / "runs" / "{{ticker}}.txt"), kind="file")},
        },
    }
    return {
        name: ResolvedStep.model_validate({"step_id": name, **common, **fields})
        for name, fields in shapes.items()
    }


def test_steps_that_do_not_opt_in_keep_their_pinned_fingerprints(tmp_path: Path) -> None:
    steps = _direct_steps(tmp_path / "repo")
    assert {name: fingerprint_step(step) for name, step in steps.items()} == _PINNED_DIRECT


def _exact_inputs_checkout(checkout: Path, *, policy: str | None) -> Path:
    """A three-step chain: ``load`` reads an authored data file, ``score`` reads
    ``load``'s output, ``report`` runs after ``score`` and reads the data file too.

    *policy* is applied to ``load`` and ``score``; ``report`` never opts in.
    """
    data = checkout / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "prices.csv").write_text("ticker,close\nAAPL,1\n", encoding="utf-8")
    return _write_process(
        checkout,
        "exact-inputs",
        [
            _code_step(
                "load",
                reuse_policy=policy,
                inputs="prices: {ref: deps.prices, kind: file}",
            ),
            _code_step(
                "score",
                reuse_policy=policy,
                inputs="loaded: {ref: load.out}",
            ),
            _code_step(
                "report",
                needs=("score",),
                inputs="prices: {ref: deps.prices, kind: file}",
            ),
        ],
        deps="prices: {path: ../data/prices.csv, as: path}",
    )


def test_a_planned_process_that_does_not_opt_in_keeps_its_pinned_fingerprints(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    process = _exact_inputs_checkout(checkout, policy=None)
    plan = _plan(process, {"RUN_ID": "r1", "RUNS_DIR": str(checkout / "runs")})
    assert {step.step_id: fingerprint_step(step) for step in plan.steps} == _PINNED_PLANNED
    assert all(step.exact_input_files is None for step in plan.steps)


# ── exact_inputs ──────────────────────────────────────────────────


def _planned_and_recorded(run_dir: Path) -> tuple[dict[str, str], dict[str, str | None]]:
    """The fingerprint each step was planned with, and the one its result recorded."""
    snapshot = read_run_plan(run_dir)
    assert snapshot is not None
    planned = {step.step_id: step.fingerprint for step in snapshot.steps}
    recorded: dict[str, str | None] = {}
    for step_id in planned:
        result = read_result_at(run_dir / STATE_DIR / TASKS_SUBDIR / step_id)
        recorded[step_id] = result.step_hash if result is not None else None
    return planned, recorded


def test_exact_inputs_reruns_a_step_whose_handler_or_input_file_changed(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    process = _exact_inputs_checkout(checkout, policy="exact_inputs")
    flows = checkout / "flows"
    runs_dir = tmp_path / "runs"
    run_id = "exact-run"
    run_dir = runs_dir / run_id

    launched = _run(process, runs_dir, run_id)
    assert launched.exit_code == 0, launched.output
    assert _invocations(run_dir) == ["load", "score", "report"]
    # `score` reads `load`'s output, which does not exist when the plan is published;
    # it is outside the hash, so the planned and executed fingerprints agree.
    planned, recorded = _planned_and_recorded(run_dir)
    assert planned == recorded

    unchanged = _run(process, runs_dir, run_id)
    assert unchanged.exit_code == 0, unchanged.output
    assert "fingerprint changed" not in unchanged.output
    assert _invocations(run_dir) == ["load", "score", "report"]

    # An opted-in step's handler is in its fingerprint: editing it re-runs the step
    # and its downstream, and leaves the upstream reused.
    with (flows / "score.py").open("a", encoding="utf-8") as fh:
        fh.write("# edited\n")
    edited = _run(process, runs_dir, run_id)
    assert edited.exit_code == 0, edited.output
    assert "Step 'score': fingerprint changed" in edited.output
    assert _invocations(run_dir) == ["load", "score", "report", "score", "report"]
    planned, recorded = _planned_and_recorded(run_dir)
    assert planned == recorded

    # A step that does not opt in is still blind to its handler.
    with (flows / "report.py").open("a", encoding="utf-8") as fh:
        fh.write("# edited\n")
    blind = _run(process, runs_dir, run_id)
    assert blind.exit_code == 0, blind.output
    assert _invocations(run_dir) == ["load", "score", "report", "score", "report"]

    # An authored input file is in an opted-in step's fingerprint.
    (checkout / "data" / "prices.csv").write_text("ticker,close\nAAPL,2\n", encoding="utf-8")
    changed = _run(process, runs_dir, run_id)
    assert changed.exit_code == 0, changed.output
    assert "Step 'load': fingerprint changed" in changed.output
    assert _invocations(run_dir)[5:] == ["load", "score", "report"]
    planned, recorded = _planned_and_recorded(run_dir)
    assert planned == recorded


def test_exact_inputs_names_the_handler_and_the_authored_inputs(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    process = _exact_inputs_checkout(checkout, policy="exact_inputs")
    plan = _plan(process, {"RUN_ID": "r1", "RUNS_DIR": str(tmp_path / "runs")})
    files = {step.step_id: step.exact_input_files for step in plan.steps}
    flows = (checkout / "flows").resolve()

    assert files == {
        "load": {
            "handler": str(flows / "load.py"),
            "inputs.prices": str((checkout / "data" / "prices.csv").resolve()),
        },
        # `score`'s only input is `load`'s output, which the run produces.
        "score": {"handler": str(flows / "score.py")},
        "report": None,
    }


_EXCLUSIONS = """\
---
process:
  name: exclusions
  defaults:
    reuse_policy: exact_inputs
  deps:
    roster: {path: ./roster.md, as: path}
    cfg: {path: ./cfg.yaml, as: path}
  steps:
    - id: scan
      mode: code
      handler: "scan.py:run"
      for_each:
        over: roster
        bind: ticker
        bind_fields: [ticker]
        key: "{{ticker}}"
      inputs:
        roster: {ref: deps.roster, kind: file}
        cfg: {ref: deps.cfg, kind: file}
        cache: {path: "{{run.dir}}/cache", kind: directory}
        per_item: {path: "{{run.dir}}/items/{{ticker}}.txt"}
      outputs:
        out: {path: "{{run.dir}}/{{ticker}}.txt", kind: file}
    - id: summarize
      mode: code
      handler: "summarize.py:run"
      needs: [scan]
      inputs:
        outcomes:
          path: "{{run.dir}}/summary/outcomes.yaml"
          collect: scan
          require: finished
        ledger: {path: "{{run.dir}}/ledger.txt", kind: file}
      outputs:
        ledger: {path: "{{run.dir}}/ledger.txt", kind: file}
---
# Exclusions
"""


def test_exact_inputs_leaves_out_rosters_collected_documents_and_own_outputs(
    tmp_path: Path,
) -> None:
    """Hashing any of these would move the fingerprint as a matter of course.

    The roster changes as items are added, and item discovery already handles that; a
    collected document is rebuilt before each run and has its own digest; a file the
    step itself rewrites would invalidate the step on every resume. A templated path
    and a directory have no single file to read.
    """
    flows = tmp_path / "checkout" / "flows"
    flows.mkdir(parents=True)
    (tmp_path / "checkout" / ".git").mkdir()
    (flows / "roster.md").write_text(_ROSTER, encoding="utf-8")
    (flows / "cfg.yaml").write_text("threshold: 1\n", encoding="utf-8")
    for name in ("scan", "summarize"):
        (flows / f"{name}.py").write_text(_handler(name), encoding="utf-8")
    process = flows / "exclusions.process.md"
    process.write_text(_EXCLUSIONS, encoding="utf-8")

    plan = _plan(process, {"RUN_ID": "r1", "RUNS_DIR": str(tmp_path / "runs")})
    files = {step.step_id: step.exact_input_files for step in plan.steps}
    resolved = flows.resolve()
    assert files == {
        "scan": {"handler": str(resolved / "scan.py"), "inputs.cfg": str(resolved / "cfg.yaml")},
        "summarize": {"handler": str(resolved / "summarize.py")},
    }


def _single_exact_step(tmp_path: Path, handler: str) -> Path:
    flows = tmp_path / "flows"
    flows.mkdir(parents=True)
    process = flows / "module.process.md"
    process.write_text(
        "---\n"
        "process:\n"
        "  name: module\n"
        "  steps:\n"
        "    - id: s1\n"
        "      mode: code\n"
        f'      handler: "{handler}"\n'
        "      reuse_policy: exact_inputs\n"
        "      outputs:\n"
        '        out: {path: "{{run.dir}}/s1.txt", kind: file}\n'
        "---\n# Module\n",
        encoding="utf-8",
    )
    return process


def test_exact_inputs_locates_a_module_path_handler(tmp_path: Path) -> None:
    process = _single_exact_step(tmp_path, "metaproc.engine.dep_state:fingerprint_step")
    plan = _plan(process, {"RUN_ID": "r1", "RUNS_DIR": str(tmp_path / "runs")})
    assert plan.steps[0].exact_input_files == {"handler": str(Path(dep_state.__file__).resolve())}


def test_exact_inputs_refuses_a_handler_module_it_cannot_find(tmp_path: Path) -> None:
    process = _single_exact_step(tmp_path, "metaproc_no_such_package.handlers:run")
    with pytest.raises(ValueError, match=r"step 's1'.*metaproc_no_such_package\.handlers"):
        _plan(process, {"RUN_ID": "r1", "RUNS_DIR": str(tmp_path / "runs")})


def test_a_missing_exact_input_is_hashed_as_absent(tmp_path: Path) -> None:
    """A file that does not exist yet still fingerprints, and its arrival is a change."""
    data = tmp_path / "data.csv"
    step = ResolvedStep.model_validate(
        {
            "step_id": "s1",
            "mode": "code",
            "handler": "h.py:run",
            "reuse_policy": "exact_inputs",
            "exact_input_files": {"inputs.data": str(data)},
        }
    )
    absent = fingerprint_step(step)
    data.write_bytes(b"")
    empty = fingerprint_step(step)
    data.write_text("a,b\n", encoding="utf-8")
    filled = fingerprint_step(step)
    assert len({absent, empty, filled}) == 3
