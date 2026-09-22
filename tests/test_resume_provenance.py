"""Resume identity versus provenance inputs.

A process input declared ``provenance: true`` records how a run executed, such as the
code revision that launched it, rather than what the run is. A resume may change its
value; every other resolved variable stays part of the identity ``run-config.yaml``
records, and changing one refuses the resume.

The end-to-end tests drive ``metaproc run-process`` repeatedly against one ``RUN_ID``: a
launch, then resumes that change the launch values or edit the process spec.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest
from strif import atomic_output_file
from typer.testing import CliRunner, Result

from metaproc.cli import app
from metaproc.commands.helpers import load_process_spec
from metaproc.commands.run_process import (
    _resume_variable_identity,
    _validate_run_config,
    _write_run_config,
)
from metaproc.engine.build_plan import build_plan
from metaproc.engine.dep_state import fingerprint_step
from metaproc.engine.process_scope import expand_process_vars
from metaproc.engine.validation import validate_scope_collisions
from metaproc.errors import CLIError
from metaproc.io import iter_jsonl_objects, read_yaml_file, to_yaml_string
from metaproc.models.authored import ProcessSpec
from metaproc.paths import RUN_CONFIG_FILE, STATE_DIR, dispatch_config_changes_log

_HANDLERS = '''\
"""Code handler for the provenance resume tests."""

from __future__ import annotations

from pathlib import Path


def record(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "invocations.log").open("a") as fh:
        fh.write("record\\n")
    (run_dir / "record.txt").write_text(variables["DATASET"] + "\\n")
'''


def _write_process(
    process_dir: Path,
    *,
    provenance: bool = True,
    build_label_default: str = "label-a",
    mode_default: str = "mode-a",
    extra_inputs: str = "",
) -> Path:
    """Write the test process: two provenance inputs and two identity inputs.

    ``code_revision`` and ``build_label`` are provenance when *provenance* is true;
    ``dataset`` and ``mode`` are always identity. ``build_label`` and ``mode`` are
    optional with a literal ``default:``, so editing a default changes the resolved set
    with no ``--var`` changing.
    """
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "handlers.py").write_text(_HANDLERS, encoding="utf-8")
    flag = "true" if provenance else "false"
    inputs = textwrap.dedent(
        f"""\
        dataset:
          param: DATASET
          as: string
        code_revision:
          param: CODE_REVISION
          as: string
          provenance: {flag}
        build_label:
          param: BUILD_LABEL
          as: string
          required: false
          default: {build_label_default}
          provenance: {flag}
        mode:
          param: MODE
          as: string
          required: false
          default: {mode_default}
        """
    ) + textwrap.dedent(extra_inputs)
    spec = (
        "---\n"
        "process:\n"
        "  name: provenance-resume\n"
        "  inputs:\n"
        + textwrap.indent(inputs, "    ")
        + textwrap.indent(
            textwrap.dedent(
                """\
                steps:
                  - id: record
                    mode: code
                    handler: "handlers.py:record"
                    outputs:
                      out:
                        path: "{{run.dir}}/record.txt"
                        kind: file
                """
            ),
            "  ",
        )
        + "---\n# Provenance Resume Fixture\n"
    )
    path = process_dir / "provenance-resume.process.md"
    path.write_text(spec, encoding="utf-8")
    return path


def _run(process_path: Path, runs_dir: Path, run_id: str, **variables: str) -> Result:
    args = [
        "run-process",
        str(process_path),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        f"RUN_ID={run_id}",
        "--backend",
        "local",
    ]
    for name, value in variables.items():
        args.extend(["--var", f"{name}={value}"])
    return CliRunner().invoke(app, args)


def _message(result: Result) -> str:
    return f"{result.output}\n{result.exception}"


def _invocations(run_dir: Path) -> list[str]:
    return (run_dir / "invocations.log").read_text().splitlines()


def _advanced(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the resume's provenance-advance INFO lines, newest capture only."""
    return [r.getMessage() for r in caplog.records if "advances provenance" in r.getMessage()]


def _provenance_changes(run_dir: Path) -> list[list[dict[str, object]]]:
    """Return the ``changes`` of each ``provenance_advance`` event, in write order."""
    log_path = dispatch_config_changes_log(run_dir)
    if not log_path.is_file():
        return []
    return [
        event["changes"]
        for event in iter_jsonl_objects(log_path)
        if event.get("event") == "provenance_advance" and "changes" in event
    ]


def _launch(tmp_path: Path, *, provenance: bool = True) -> tuple[Path, Path, str]:
    process_path = _write_process(tmp_path / "proc", provenance=provenance)
    runs_dir = tmp_path / "runs"
    run_id = "provenance-run"
    launched = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION="rev-a")
    assert launched.exit_code == 0, _message(launched)
    assert _invocations(runs_dir / run_id) == ["record"]
    return process_path, runs_dir, run_id


# ── End to end through run-process ────────────────────────────────


def test_changed_provenance_input_resumes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id

    with caplog.at_level(logging.INFO, logger="metaproc.commands.run_process"):
        resumed = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION="rev-b")

    assert resumed.exit_code == 0, _message(resumed)
    # The completed step is reused, not re-run.
    assert _invocations(run_dir) == ["record"]
    # One line for the input, naming both the logical name and the operator's alias.
    assert _advanced(caplog) == [
        "Resume advances provenance input code_revision (CODE_REVISION): 'rev-a' -> 'rev-b'"
    ]
    # run-config.yaml keeps the values the run was launched with.
    recorded = read_yaml_file(run_dir / STATE_DIR / RUN_CONFIG_FILE)["variables"]
    assert recorded["CODE_REVISION"] == "rev-a"
    assert recorded["code_revision"] == "rev-a"
    # The run directory records the move durably, apart from the process output.
    events = list(iter_jsonl_objects(dispatch_config_changes_log(run_dir)))
    assert [event["event"] for event in events] == ["provenance_advance"]
    assert events[0]["changes"] == [
        {
            "field": "code_revision",
            "param": "CODE_REVISION",
            "diff": {"old": "rev-a", "new": "rev-b"},
        }
    ]


def test_each_advance_is_recorded_against_the_last_effective_value(tmp_path: Path) -> None:
    """``old`` is what the previous resume ran with, not the launch value.

    ``run-config.yaml`` is never rewritten, so diffing every resume against it would
    make the log unable to say what the latest resume ran with: a repeat would re-emit
    an identical event, a chain would never record ``rev-b -> rev-c``, and a return to
    the launch value would record nothing at all while the last entry still claimed the
    advanced value.
    """
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id

    for revision in ("rev-b", "rev-c", "rev-c", "rev-a"):
        resumed = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION=revision)
        assert resumed.exit_code == 0, _message(resumed)
    # Every resume reused the completed step.
    assert _invocations(run_dir) == ["record"]

    def diff(old: str, new: str) -> list[dict[str, object]]:
        return [
            {"field": "code_revision", "param": "CODE_REVISION", "diff": {"old": old, "new": new}}
        ]

    assert _provenance_changes(run_dir) == [
        diff("rev-a", "rev-b"),  # against the launch value, no event yet
        diff("rev-b", "rev-c"),  # a chain, not a second diff from rev-a
        # The repeat at rev-c moved nothing and wrote nothing.
        diff("rev-c", "rev-a"),  # the return to the launch value is still a move
    ]
    # run-config.yaml is untouched throughout.
    recorded = read_yaml_file(run_dir / STATE_DIR / RUN_CONFIG_FILE)["variables"]
    assert recorded["code_revision"] == "rev-a"


def test_a_resume_with_the_launch_values_records_no_provenance_advance(tmp_path: Path) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)

    resumed = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION="rev-a")

    assert resumed.exit_code == 0, _message(resumed)
    changes = dispatch_config_changes_log(runs_dir / run_id)
    events = list(iter_jsonl_objects(changes)) if changes.is_file() else []
    assert [event for event in events if event["event"] == "provenance_advance"] == []


def test_changed_identity_input_still_refuses(tmp_path: Path) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)

    refused = _run(process_path, runs_dir, run_id, DATASET="ds-2", CODE_REVISION="rev-b")

    assert refused.exit_code != 0
    message = _message(refused)
    assert "Resume mismatch: run-config.yaml identity variables changed: DATASET, dataset." in (
        message
    )
    # The provenance input changed too, and is neither named nor the cause.
    assert "CODE_REVISION" not in message.split("may change on a resume")[0]
    assert "declares `provenance: true` may change on a resume" in message
    assert "(this process declares: BUILD_LABEL, CODE_REVISION, build_label, code_revision)" in (
        message
    )
    # Values stay out of the refusal.
    assert "ds-1" not in message and "ds-2" not in message
    assert _invocations(runs_dir / run_id) == ["record"]


def test_edited_default_on_provenance_input_resumes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    _write_process(process_path.parent, build_label_default="label-b")

    with caplog.at_level(logging.INFO, logger="metaproc.commands.run_process"):
        resumed = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION="rev-a")

    assert resumed.exit_code == 0, _message(resumed)
    assert _invocations(runs_dir / run_id) == ["record"]
    assert _advanced(caplog) == [
        "Resume advances provenance input build_label (BUILD_LABEL): 'label-a' -> 'label-b'"
    ]


def test_edited_default_on_identity_input_still_refuses(tmp_path: Path) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    _write_process(process_path.parent, mode_default="mode-b")

    refused = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION="rev-a")

    assert refused.exit_code != 0
    message = _message(refused)
    assert "Resume mismatch: run-config.yaml identity variables changed: MODE, mode." in message
    assert "original --var values and input defaults" in message
    assert "declares `provenance: true` may change on a resume" in message
    assert _invocations(runs_dir / run_id) == ["record"]


def test_newly_added_identity_input_still_refuses(tmp_path: Path) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    _write_process(
        process_path.parent,
        extra_inputs="""\
            region:
              param: REGION
              as: string
              required: false
              default: us-east
            """,
    )

    refused = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION="rev-b")

    assert refused.exit_code != 0
    message = _message(refused)
    # Values stay out of the refusal, so each name says whether the resume added it.
    assert (
        "Resume mismatch: run-config.yaml identity variables changed: "
        "REGION (added), region (added)." in message
    )
    assert "CODE_REVISION" not in message.split("may change on a resume")[0]
    assert _invocations(runs_dir / run_id) == ["record"]


def test_newly_added_provenance_input_resumes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    _write_process(
        process_path.parent,
        extra_inputs="""\
            framework_revision:
              param: FRAMEWORK_REVISION
              as: string
              provenance: true
            """,
    )

    with caplog.at_level(logging.INFO, logger="metaproc.commands.run_process"):
        resumed = _run(
            process_path,
            runs_dir,
            run_id,
            DATASET="ds-1",
            CODE_REVISION="rev-a",
            FRAMEWORK_REVISION="fw-1",
        )

    assert resumed.exit_code == 0, _message(resumed)
    assert _invocations(runs_dir / run_id) == ["record"]
    assert _advanced(caplog) == [
        (
            "Resume advances provenance input framework_revision (FRAMEWORK_REVISION): "
            "<unset> -> 'fw-1'"
        )
    ]


def test_spec_without_provenance_inputs_behaves_as_before(tmp_path: Path) -> None:
    """With no input declared provenance, every resolved variable is identity.

    An unchanged resume is accepted and reuses the completed step; a changed variable
    or an edited ``default:`` refuses, as it always has.
    """
    process_path, runs_dir, run_id = _launch(tmp_path, provenance=False)
    run_dir = runs_dir / run_id

    unchanged = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION="rev-a")
    assert unchanged.exit_code == 0, _message(unchanged)
    assert _invocations(run_dir) == ["record"]

    changed = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION="rev-b")
    assert changed.exit_code != 0
    message = _message(changed)
    assert (
        "Resume mismatch: run-config.yaml identity variables changed: CODE_REVISION, "
        "code_revision." in message
    )
    assert "(this process declares none)" in message

    _write_process(process_path.parent, provenance=False, build_label_default="label-b")
    edited = _run(process_path, runs_dir, run_id, DATASET="ds-1", CODE_REVISION="rev-a")
    assert edited.exit_code != 0
    assert "identity variables changed: BUILD_LABEL, build_label." in _message(edited)
    assert _invocations(run_dir) == ["record"]


# ── The authored field and the names it contributes ──────────────


def test_provenance_input_names_cover_logical_name_and_param_alias() -> None:
    spec = ProcessSpec.model_validate(
        {
            "name": "names",
            "inputs": {
                "code_revision": {"param": "CODE_REVISION", "as": "string", "provenance": True},
                "notes": {"path": "notes.md", "as": "path", "provenance": True},
                "dataset": {"param": "DATASET", "as": "string"},
            },
        }
    )
    # An input is provenance only where the spec says so.
    assert spec.inputs["code_revision"].provenance is True
    assert spec.inputs["dataset"].provenance is False
    # One entry per input, carrying the alias a log entry names alongside it.
    assert spec.provenance_inputs == {"code_revision": "CODE_REVISION", "notes": None}
    # Flattened to the variable names resolution writes.
    assert spec.provenance_input_names == {"code_revision", "CODE_REVISION", "notes"}
    assert ProcessSpec.model_validate({"name": "none"}).provenance_inputs == {}
    assert ProcessSpec.model_validate({"name": "none"}).provenance_input_names == set()


def test_a_provenance_name_may_not_also_be_an_identity_input_name() -> None:
    """A name is provenance or identity, never both.

    ``X`` below leaves the resume comparison as ``revision``'s alias, and it is the
    identity input's only name, so without this rejection that input could change on a
    resume unnoticed.
    """
    spec = ProcessSpec.model_validate(
        {
            "name": "shadow",
            "inputs": {
                "revision": {"param": "X", "as": "string", "provenance": True},
                "X": {"as": "string"},
            },
        }
    )
    assert validate_scope_collisions(spec) == [
        (
            "provenance input 'revision': name 'X' is also used by an input that is not "
            "`provenance: true`, which would drop that input out of resume identity"
        )
    ]

    shared_param = ProcessSpec.model_validate(
        {
            "name": "shared-param",
            "inputs": {
                "revision": {"param": "X", "as": "string", "provenance": True},
                "other": {"param": "X", "as": "string"},
            },
        }
    )
    assert len(validate_scope_collisions(shared_param)) == 1

    clean = ProcessSpec.model_validate(
        {
            "name": "clean",
            "inputs": {
                "revision": {"param": "CODE_REVISION", "as": "string", "provenance": True},
                "dataset": {"param": "DATASET", "as": "string"},
            },
        }
    )
    assert validate_scope_collisions(clean) == []


# ── The identity comparison directly ──────────────────────────────


def _config(tmp_path: Path, variables: dict[str, str]) -> Path:
    config_path = tmp_path / STATE_DIR / RUN_CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"process": "mine", "run_dir": str(tmp_path), "run_id": "run-1", "variables": variables}
    with atomic_output_file(config_path) as tmp:
        Path(tmp).write_text(to_yaml_string(data), encoding="utf-8")
    return config_path


def test_identity_excludes_provenance_names_and_keeps_runs_dir_normalization() -> None:
    variables = {
        "RUNS_DIR": "/mnt/disks/filestore/runs",
        "RUN_ID": "run-1",
        "code_revision": "rev-a",
        "CODE_REVISION": "rev-a",
    }
    assert _resume_variable_identity(variables, {"code_revision", "CODE_REVISION"}) == {
        "RUNS_DIR": "/mnt/filestore/runs",
        "RUN_ID": "run-1",
    }
    assert _resume_variable_identity(variables, frozenset()) == {
        **variables,
        "RUNS_DIR": "/mnt/filestore/runs",
    }


def test_resolved_defaults_follow_the_input_declaration(tmp_path: Path) -> None:
    """The comparison sees resolved variables; an edited default is judged by its input."""
    authored = {
        "name": "mine",
        "inputs": {
            "build_label": {
                "param": "BUILD_LABEL",
                "as": "string",
                "required": False,
                "default": "label-a",
                "provenance": True,
            },
            "mode": {"param": "MODE", "as": "string", "required": False, "default": "mode-a"},
        },
    }
    launch_spec = ProcessSpec.model_validate(authored)
    config_path = _config(tmp_path, expand_process_vars(launch_spec, {"RUN_ID": "run-1"}))

    edited = ProcessSpec.model_validate(authored)
    edited.inputs["build_label"].default = "label-b"
    _validate_run_config(
        config_path,
        process_name="mine",
        run_dir=tmp_path,
        variables=expand_process_vars(edited, {"RUN_ID": "run-1"}),
        provenance_names=edited.provenance_input_names,
    )

    edited.inputs["mode"].default = "mode-b"
    with pytest.raises(CLIError, match=r"identity variables changed: MODE, mode\."):
        _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=tmp_path,
            variables=expand_process_vars(edited, {"RUN_ID": "run-1"}),
            provenance_names=edited.provenance_input_names,
        )


def test_removed_provenance_variable_resumes_and_removed_identity_refuses(
    tmp_path: Path,
) -> None:
    config_path = _config(tmp_path, {"RUN_ID": "run-1", "NOTE": "n-1", "DATASET": "ds-1"})

    _validate_run_config(
        config_path,
        process_name="mine",
        run_dir=tmp_path,
        variables={"RUN_ID": "run-1", "DATASET": "ds-1"},
        provenance_names={"NOTE"},
    )
    with pytest.raises(CLIError, match=r"identity variables changed: DATASET \(removed\)\."):
        _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=tmp_path,
            variables={"RUN_ID": "run-1", "NOTE": "n-1"},
            provenance_names={"NOTE"},
        )


def test_write_run_config_threads_provenance_inputs_to_validation(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1" / "mine"
    run_dir.mkdir(parents=True)

    def write(revision: str, provenance_inputs: dict[str, str | None]) -> None:
        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-1",
            variables={"RUN_ID": "run-1", "CODE_REVISION": revision},
            backend="local",
            variant=None,
            provenance_inputs=provenance_inputs,
        )

    write("rev-a", {"code_revision": "CODE_REVISION"})
    write("rev-b", {"code_revision": "CODE_REVISION"})
    with pytest.raises(CLIError, match=r"identity variables changed: CODE_REVISION\."):
        write("rev-c", {})


def test_an_unwritable_changes_log_fails_the_resume_with_the_path(tmp_path: Path) -> None:
    """A log the process cannot append to is an error naming the file, not a traceback."""
    run_dir = tmp_path / "run-1" / "mine"
    run_dir.mkdir(parents=True)

    def write(revision: str) -> None:
        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-1",
            variables={"RUN_ID": "run-1", "CODE_REVISION": revision},
            backend="local",
            variant=None,
            provenance_inputs={"code_revision": "CODE_REVISION"},
        )

    write("rev-a")
    blocked = dispatch_config_changes_log(run_dir)
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.mkdir()
    with pytest.raises(CLIError, match=r"Cannot record the resume change event in .*"):
        write("rev-b")


def test_a_damaged_changes_log_falls_back_to_the_launch_value(tmp_path: Path) -> None:
    """A line this reader cannot interpret means "nothing recorded", never a failure."""
    run_dir = tmp_path / "run-1" / "mine"
    run_dir.mkdir(parents=True)

    def write(revision: str) -> None:
        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-1",
            variables={"RUN_ID": "run-1", "CODE_REVISION": revision},
            backend="local",
            variant=None,
            provenance_inputs={"code_revision": "CODE_REVISION"},
        )

    write("rev-a")
    changes = dispatch_config_changes_log(run_dir)
    changes.parent.mkdir(parents=True, exist_ok=True)
    changes.write_text(
        "not json\n"
        '{"event": "provenance_advance"}\n'
        '{"event": "provenance_advance", "changes": [{"field": "code_revision"}]}\n',
        encoding="utf-8",
    )

    write("rev-b")

    # Nothing legible said otherwise, so the launch value is the last effective one.
    assert _provenance_changes(run_dir)[-1] == [
        {
            "field": "code_revision",
            "param": "CODE_REVISION",
            "diff": {"old": "rev-a", "new": "rev-b"},
        }
    ]


# ── The fingerprint caveat ────────────────────────────────────────


def test_advancing_a_provenance_input_changes_only_the_fingerprints_that_bind_it(
    tmp_path: Path,
) -> None:
    """Leaving resume identity does not exempt a value from step fingerprints.

    A value bound through a composite step's ``with:`` stays a template in the resolved
    plan, so advancing it leaves that step's fingerprint alone. A value substituted into
    a resolved field such as ``env:`` is in the payload, so the step re-runs with its
    downstream on the next resume. Absolute fingerprint values are not asserted: a
    composite step's payload carries its absolute ``uses_path``, so its hash depends on
    where the process files live.
    """
    (tmp_path / "child.process.md").write_text(
        textwrap.dedent("""\
            ---
            process:
              name: child
              inputs:
                rev: {param: REV, as: string}
              steps:
                - id: leaf
                  mode: code
                  command: "true"
            ---
            # Child
            """),
        encoding="utf-8",
    )
    process_path = tmp_path / "parent.process.md"
    process_path.write_text(
        textwrap.dedent("""\
            ---
            process:
              name: parent
              inputs:
                code_revision: {param: CODE_REVISION, as: string, provenance: true}
              deps:
                child: {path: ./child.process.md, as: path}
              steps:
                - id: via-with
                  mode: composite
                  uses: deps.child
                  with:
                    rev: "{{code_revision}}"
                - id: via-env
                  mode: code
                  command: "true"
                  env:
                    REV: "{{code_revision}}"
            ---
            # Parent
            """),
        encoding="utf-8",
    )
    spec = load_process_spec(process_path)

    def fingerprints(revision: str) -> dict[str, str]:
        params = expand_process_vars(
            spec,
            {
                "CODE_REVISION": revision,
                "RUN_ID": "run-1",
                "RUNS_DIR": str(tmp_path / "runs"),
            },
        )
        plan = build_plan(spec, params, process_path=process_path)
        return {step.step_id: fingerprint_step(step) for step in plan.steps}

    before, after = fingerprints("rev-a"), fingerprints("rev-b")

    assert before["via-with"] == after["via-with"]
    assert before["via-env"] != after["via-env"]
