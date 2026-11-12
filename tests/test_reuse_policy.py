"""``reuse_policy`` decides whether a completed step is reused on a later run.

Spec: ``src/metaproc/docs/metaproc-design.md`` §10.2 and §10.3.

A spec may declare ``validated_outputs`` (the default), ``exact_inputs`` or ``never``;
any other value is rejected when the spec loads.

- ``never`` re-runs a scalar step on every walk that reaches it. Its downstream steps
  are judged by their own policy. Planning refuses it on a mapped step and on any step
  that runs inside the item scopes of a mapped composite.
- ``exact_inputs`` is specified but not implemented, so planning refuses it.
- The pinned values below are the fingerprints these fixtures have, so a change that
  would re-run completed steps in every resumable run fails here first.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner, Result

from metaproc.cli import app
from metaproc.commands.helpers import load_process_spec
from metaproc.engine.build_plan import build_plan
from metaproc.engine.dep_state import fingerprint_step
from metaproc.engine.process_scope import expand_process_vars
from metaproc.errors import ValidationError as MetaprocValidationError
from metaproc.models.authored import IOSpec, ProcessDefaults, ProcessStep
from metaproc.models.plan import FanOut, Plan, ResolvedAdapter, ResolvedStep
from metaproc.viz_loader import load_plan_bundle

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


def _run(process_path: Path, runs_dir: Path, run_id: str, *extra: str) -> Result:
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
            *extra,
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

    narrowed = _run(process, runs_dir, "never-ancestor-run", "--from", "last", "--force")
    assert narrowed.exit_code == 0, narrowed.output
    assert _invocations(run_dir) == ["stamp", "last", "last"]


def _stamp_then_last(tmp_path: Path, name: str) -> Path:
    """``stamp`` declares ``never``; ``last`` runs after it under the default policy."""
    return _write_process(
        tmp_path / "checkout",
        name,
        [_code_step("stamp", reuse_policy="never"), _code_step("last", needs=("stamp",))],
    )


def test_from_alone_reruns_a_never_step_it_selects(tmp_path: Path) -> None:
    """``--from`` without ``--force`` reuses a completed step it selects, except one whose
    policy is ``never``; the steps after it are still judged by their own policy."""
    process = _stamp_then_last(tmp_path, "never-from")
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "r1"
    assert _run(process, runs_dir, "r1").exit_code == 0

    narrowed = _run(process, runs_dir, "r1", "--from", "stamp")
    assert narrowed.exit_code == 0, narrowed.output
    assert "Step 'stamp': completed earlier; reuse_policy never — running again" in narrowed.output
    assert "Step 'last': already completed" in narrowed.output
    assert _invocations(run_dir) == ["stamp", "last", "stamp"]


def test_a_satisfied_override_wins_over_never(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``metaproc override <step> --satisfied`` is checked before reuse, ``never`` included."""
    process = _stamp_then_last(tmp_path, "never-override")
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "r1"
    assert _run(process, runs_dir, "r1").exit_code == 0

    monkeypatch.setenv("RUNS_DIR", str(runs_dir))
    override = CliRunner().invoke(
        app,
        [
            "override",
            "r1",
            "stamp",
            "--satisfied",
            "--note",
            "accepted by hand",
            "--process",
            str(process),
            "--var",
            f"RUNS_DIR={runs_dir}",
        ],
    )
    assert override.exit_code == 0, override.output

    resumed = _run(process, runs_dir, "r1")
    assert resumed.exit_code == 0, resumed.output
    assert "Step 'stamp': satisfied via override — skipping" in resumed.output
    assert _invocations(run_dir) == ["stamp", "last"]


def test_never_on_a_manual_step_accepts_its_recorded_acknowledgment_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``never`` manual step re-enters its executor on every launch, which accepts the
    acknowledgment already recorded instead of waiting for a new one."""
    monkeypatch.setattr("metaproc.commands.run_process.MANUAL_ACK_TIMEOUT_S", 0)
    flows = tmp_path / "flows"
    flows.mkdir()
    process = flows / "manual.process.md"
    process.write_text(
        "---\n"
        "process:\n"
        "  name: manual\n"
        "  steps:\n"
        "    - id: approve\n"
        "      mode: manual\n"
        "      reuse_policy: never\n"
        "---\n"
        "# Manual\n",
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"
    acknowledged = CliRunner().invoke(
        app,
        [
            "run-step",
            str(process),
            "--step",
            "approve",
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            "RUN_ID=r1",
            "--operator",
            "alice",
        ],
    )
    assert acknowledged.exit_code == 0, acknowledged.output

    for _ in range(2):
        launched = _run(process, runs_dir, "r1")
        assert launched.exit_code == 0, launched.output
        assert (
            "Step 'approve': completed earlier; reuse_policy never — running again"
            in launched.output
        )
        assert "waiting for operator acknowledgment" not in launched.output


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


# ── never inside composites ───────────────────────────────────────


def _scope_handler(log: Path, name: str) -> str:
    """A handler that logs ``<name> <scope dir>`` to *log* and writes its declared output."""
    return textwrap.dedent(
        f'''\
        """Handler for the {name} step."""

        from __future__ import annotations

        from pathlib import Path


        def run(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
            out = Path(step.outputs["out"].path)  # type: ignore[attr-defined]
            out.parent.mkdir(parents=True, exist_ok=True)
            with Path({str(log)!r}).open("a", encoding="utf-8") as fh:
                fh.write("{name} " + out.parent.name + "\\n")
            out.write_text("{name} done\\n", encoding="utf-8")
        '''
    )


_ITEMS = "---\nprogress:\n  items:\n    - item: a\n    - item: b\n---\n# Items\n"

_FOR_EACH_ITEM = (
    "      for_each:\n"
    "        over: deps.items\n"
    "        bind: item\n"
    "        bind_fields: [item]\n"
    '        key: "{{item}}"\n'
)


def _child_spec(name: str, *, never: str) -> str:
    """A two-step child process: ``tick``, then ``tock``.

    *never* puts ``reuse_policy: never`` on ``tick`` (``"step"``) or in the process
    defaults (``"defaults"``).
    """
    defaults = "  defaults:\n    reuse_policy: never\n" if never == "defaults" else ""
    tick_policy = "      reuse_policy: never\n" if never == "step" else ""
    return (
        "---\n"
        "process:\n"
        f"  name: {name}\n"
        f"{defaults}"
        "  steps:\n"
        "    - id: tick\n"
        "      mode: code\n"
        '      handler: "tick.py:run"\n'
        f"{tick_policy}"
        "      outputs:\n"
        '        out: {path: "{{run.dir}}/tick.txt", kind: file}\n'
        "    - id: tock\n"
        "      mode: code\n"
        '      handler: "tock.py:run"\n'
        "      needs: [tick]\n"
        "      outputs:\n"
        '        out: {path: "{{run.dir}}/tock.txt", kind: file}\n'
        "---\n"
        f"# {name}\n"
    )


def _composite_spec(name: str, *, step_id: str, uses: str, mapped: bool) -> str:
    """A process whose one step is a composite running ``deps.<uses>``."""
    return (
        "---\n"
        "process:\n"
        f"  name: {name}\n"
        "  deps:\n"
        f"    {uses}: {{path: ./{uses}.process.md, as: path}}\n"
        "    items: {path: ./items.md, as: path}\n"
        "  steps:\n"
        f"    - id: {step_id}\n"
        "      mode: composite\n"
        f"      uses: deps.{uses}\n"
        f"{_FOR_EACH_ITEM if mapped else ''}"
        "---\n"
        f"# {name}\n"
    )


def _composite_tree(root: Path, *, mapped: bool, nested: bool, never: str) -> tuple[Path, Path]:
    """``parent`` runs composite ``wrap``; ``wrap`` runs ``leaf``, or ``middle`` when nested.

    ``wrap`` is mapped over two items when *mapped*. ``middle`` runs ``leaf`` through the
    scalar composite ``inner``. ``leaf`` is :func:`_child_spec`. Returns the parent
    process and the invocation log.
    """
    flows = root / "flows"
    flows.mkdir(parents=True)
    (root / ".git").mkdir()
    log = root / "invocations.log"
    for name in ("tick", "tock"):
        (flows / f"{name}.py").write_text(_scope_handler(log, name), encoding="utf-8")
    (flows / "items.md").write_text(_ITEMS, encoding="utf-8")
    (flows / "leaf.process.md").write_text(_child_spec("leaf", never=never), encoding="utf-8")
    if nested:
        (flows / "middle.process.md").write_text(
            _composite_spec("middle", step_id="inner", uses="leaf", mapped=False),
            encoding="utf-8",
        )
    parent = flows / "parent.process.md"
    parent.write_text(
        _composite_spec(
            "parent", step_id="wrap", uses="middle" if nested else "leaf", mapped=mapped
        ),
        encoding="utf-8",
    )
    return parent, log


def _logged(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_never_in_the_child_of_a_scalar_composite_reruns_on_every_launch(tmp_path: Path) -> None:
    """A scalar composite is entered on every walk, so its child ``never`` step runs again."""
    process, log = _composite_tree(tmp_path, mapped=False, nested=False, never="step")
    runs_dir = tmp_path / "runs"

    launched = _run(process, runs_dir, "r1")
    assert launched.exit_code == 0, launched.output
    assert _logged(log) == ["tick wrap", "tock wrap"]

    resumed = _run(process, runs_dir, "r1")
    assert resumed.exit_code == 0, resumed.output
    assert "Step 'tick': completed earlier; reuse_policy never — running again" in resumed.output
    assert "Step 'tock': already completed" in resumed.output
    assert _logged(log) == ["tick wrap", "tock wrap", "tick wrap"]


@pytest.mark.parametrize("dry_run", [False, True], ids=["launch", "dry-run"])
@pytest.mark.parametrize("nested", [False, True], ids=["child", "grandchild"])
@pytest.mark.parametrize("never", ["step", "defaults"])
def test_never_under_a_mapped_composite_is_refused_before_the_run_starts(
    tmp_path: Path, dry_run: bool, nested: bool, never: str
) -> None:
    """A completed item scope of a mapped composite is not re-entered, so a ``never``
    step anywhere below it would not run again; the launch refuses it as it refuses
    ``never`` on a mapped step, before the run directory exists."""
    process, log = _composite_tree(tmp_path, mapped=True, nested=nested, never=never)
    runs_dir = tmp_path / "runs"

    result = _run(process, runs_dir, "r1", *(["--dry-run"] if dry_run else []))

    assert isinstance(result.exception, MetaprocValidationError), result.output
    message = str(result.exception)
    source = "the step" if never == "step" else "defaults"
    assert f"step 'tick': reuse_policy 'never' (from {source})" in message
    assert "mapped composite step 'wrap'" in message
    assert not (runs_dir / "r1").exists()
    assert _logged(log) == []


def test_a_validated_bundle_load_refuses_never_under_a_mapped_composite_and_a_viewer_does_not(
    tmp_path: Path,
) -> None:
    """The refusal belongs to spec validation, so every validated composite load makes it,
    and a viewer that skips spec validation still renders the tree."""
    process, _ = _composite_tree(tmp_path, mapped=True, nested=True, never="step")
    params = {"RUN_ID": "r1", "RUNS_DIR": str(tmp_path / "runs")}

    with pytest.raises(ValueError, match="mapped composite step 'wrap'"):
        load_plan_bundle(process, params=params)

    viewed = load_plan_bundle(process, params=params, validate_spec=False)
    leaf = viewed.children["wrap"].children["inner"]
    assert [step.reuse_policy for step in leaf.plan.steps] == ["never", "validated_outputs"]


def _child_with_a_mapped_never_default(root: Path) -> Path:
    """A scalar composite ``wrap`` whose child's mapped step inherits ``never``."""
    flows = root / "flows"
    flows.mkdir(parents=True)
    (root / ".git").mkdir()
    (flows / "items.md").write_text(_ITEMS, encoding="utf-8")
    (flows / "each.py").write_text(
        _scope_handler(root / "invocations.log", "each"), encoding="utf-8"
    )
    (flows / "leaf.process.md").write_text(
        "---\n"
        "process:\n"
        "  name: leaf\n"
        "  defaults:\n"
        "    reuse_policy: never\n"
        "  deps:\n"
        "    items: {path: ./items.md, as: path}\n"
        "  steps:\n"
        "    - id: each\n"
        "      mode: code\n"
        '      handler: "each.py:run"\n'
        f"{_FOR_EACH_ITEM}"
        "      outputs:\n"
        '        out: {path: "{{run.dir}}/each-{{item}}.txt", kind: file}\n'
        "---\n"
        "# leaf\n",
        encoding="utf-8",
    )
    parent = flows / "parent.process.md"
    parent.write_text(
        _composite_spec("parent", step_id="wrap", uses="leaf", mapped=False), encoding="utf-8"
    )
    return parent


@pytest.mark.parametrize("dry_run", [False, True], ids=["launch", "dry-run"])
def test_never_inherited_by_a_mapped_step_of_a_child_process_is_refused_before_the_run_starts(
    tmp_path: Path, dry_run: bool
) -> None:
    """A child process is planned with the root's validation before anything is written,
    so a dry run refuses what the launch refuses, and neither ends in a traceback."""
    process = _child_with_a_mapped_never_default(tmp_path)
    runs_dir = tmp_path / "runs"

    result = _run(process, runs_dir, "r1", *(["--dry-run"] if dry_run else []))

    assert isinstance(result.exception, MetaprocValidationError), result.output
    assert "step 'each': reuse_policy 'never' (from defaults)" in str(result.exception)
    assert not (runs_dir / "r1").exists()


# ── Accepted values ───────────────────────────────────────────────


@pytest.mark.parametrize("value", ["always_rerun", "trust_state", "validated-outputs"])
def test_an_undocumented_reuse_policy_is_rejected(value: str) -> None:
    step = {"id": "s1", "mode": "code", "handler": "h.py:run", "reuse_policy": value}
    with pytest.raises(ValidationError, match="validated_outputs"):
        ProcessStep.model_validate(step)
    with pytest.raises(ValidationError, match="validated_outputs"):
        ProcessDefaults.model_validate({"reuse_policy": value})


def _exact_inputs_spec(tmp_path: Path, *, mapped: bool, source: str) -> tuple[Path, str]:
    """A process whose one step declares or inherits ``exact_inputs``; returns it and the step id."""
    step_policy = "exact_inputs" if source == "the step" else None
    default_policy = "exact_inputs" if source == "defaults" else None
    if mapped:
        return _mapped_spec(
            tmp_path, step_policy=step_policy, default_policy=default_policy
        ), "each"
    process = _write_process(
        tmp_path / "checkout",
        "exact",
        [_code_step("s1", reuse_policy=step_policy)],
        defaults=f"reuse_policy: {default_policy}" if default_policy else "",
    )
    return process, "s1"


@pytest.mark.parametrize("mapped", [False, True], ids=["scalar", "mapped"])
@pytest.mark.parametrize("source", ["the step", "defaults"])
def test_exact_inputs_loads_but_planning_refuses_it(
    tmp_path: Path, mapped: bool, source: str
) -> None:
    """``exact_inputs`` is specified but not implemented, so planning refuses it rather
    than let it reuse completions the way ``validated_outputs`` does. The spec still
    loads, so viewers that skip spec validation can render it."""
    process, step_id = _exact_inputs_spec(tmp_path, mapped=mapped, source=source)
    spec = load_process_spec(process)
    assert (spec.steps[0].reuse_policy or spec.defaults.reuse_policy) == "exact_inputs"

    with pytest.raises(ValueError, match="invalid reuse policies") as caught:
        _plan(process, {"RUN_ID": "r1", "RUNS_DIR": str(tmp_path / "runs")})
    message = str(caught.value)
    assert f"step '{step_id}': reuse_policy 'exact_inputs' (from {source})" in message
    assert "not yet implemented" in message
    assert "mp-gg7r" in message


@pytest.mark.parametrize("dry_run", [False, True], ids=["launch", "dry-run"])
def test_run_process_refuses_exact_inputs_before_the_run_starts(
    tmp_path: Path, dry_run: bool
) -> None:
    process, _ = _exact_inputs_spec(tmp_path, mapped=False, source="the step")
    runs_dir = tmp_path / "runs"

    result = _run(process, runs_dir, "r1", *(["--dry-run"] if dry_run else []))

    assert isinstance(result.exception, MetaprocValidationError), result.output
    assert "reuse_policy 'exact_inputs'" in str(result.exception)
    assert not (runs_dir / "r1").exists()


# ── Fingerprints are pinned ───────────────────────────────────────

# A change to any of these re-runs every completed step of that shape in every
# resumable run, so it needs a changelog entry that says so.
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


def test_resolved_step_fingerprints_are_pinned(tmp_path: Path) -> None:
    steps = _direct_steps(tmp_path / "repo")
    assert {name: fingerprint_step(step) for name, step in steps.items()} == _PINNED_DIRECT


def _three_step_checkout(checkout: Path) -> Path:
    """A three-step chain: ``load`` reads an authored data file, ``score`` reads
    ``load``'s output, ``report`` runs after ``score`` and reads the data file too."""
    data = checkout / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "prices.csv").write_text("ticker,close\nAAPL,1\n", encoding="utf-8")
    return _write_process(
        checkout,
        "pinned",
        [
            _code_step("load", inputs="prices: {ref: deps.prices, kind: file}"),
            _code_step("score", inputs="loaded: {ref: load.out}"),
            _code_step(
                "report",
                needs=("score",),
                inputs="prices: {ref: deps.prices, kind: file}",
            ),
        ],
        deps="prices: {path: ../data/prices.csv, as: path}",
    )


def test_planned_step_fingerprints_are_pinned(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    process = _three_step_checkout(checkout)
    plan = _plan(process, {"RUN_ID": "r1", "RUNS_DIR": str(checkout / "runs")})
    assert {step.step_id: fingerprint_step(step) for step in plan.steps} == _PINNED_PLANNED
