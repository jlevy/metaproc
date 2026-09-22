"""Resume identity versus provenance inputs.

A process input declared ``provenance: true`` records how a run executed, such as the
code revision that launched it, rather than what the run is. A resume may change its
value; every other resolved variable stays part of the identity ``run-config.yaml``
records, and changing one refuses the resume.

The end-to-end tests drive ``metaproc run-process`` twice against one ``RUN_ID``: a
launch, then a resume that changes the launch values or edits the process spec.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest
from strif import atomic_output_file
from typer.testing import CliRunner, Result

from metaproc.cli import app
from metaproc.commands.run_process import (
    _resume_variable_identity,
    _validate_run_config,
    _write_run_config,
)
from metaproc.engine.process_scope import expand_process_vars
from metaproc.errors import CLIError
from metaproc.io import iter_jsonl_objects, read_yaml_file, to_yaml_string
from metaproc.models.authored import ProcessInput, ProcessSpec
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
    # The run's log records the move under both names resolution writes.
    advanced = [r.getMessage() for r in caplog.records if "advances provenance" in r.getMessage()]
    assert advanced == [
        "Resume advances provenance input CODE_REVISION: 'rev-a' -> 'rev-b'",
        "Resume advances provenance input code_revision: 'rev-a' -> 'rev-b'",
    ]
    # run-config.yaml keeps the values the run was launched with.
    recorded = read_yaml_file(run_dir / STATE_DIR / RUN_CONFIG_FILE)["variables"]
    assert recorded["CODE_REVISION"] == "rev-a"
    assert recorded["code_revision"] == "rev-a"
    # The run directory records the move durably, apart from the process output.
    events = list(iter_jsonl_objects(dispatch_config_changes_log(run_dir)))
    assert [event["event"] for event in events] == ["provenance_advance"]
    assert events[0]["changes"] == [
        {"field": "CODE_REVISION", "diff": {"old": "rev-a", "new": "rev-b"}},
        {"field": "code_revision", "diff": {"old": "rev-a", "new": "rev-b"}},
    ]


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
    advanced = [r.getMessage() for r in caplog.records if "advances provenance" in r.getMessage()]
    assert advanced == [
        "Resume advances provenance input BUILD_LABEL: 'label-a' -> 'label-b'",
        "Resume advances provenance input build_label: 'label-a' -> 'label-b'",
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
    assert "Resume mismatch: run-config.yaml identity variables changed: REGION, region." in (
        message
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
    advanced = [r.getMessage() for r in caplog.records if "advances provenance" in r.getMessage()]
    assert advanced == [
        "Resume advances provenance input FRAMEWORK_REVISION: <unset> -> 'fw-1'",
        "Resume advances provenance input framework_revision: <unset> -> 'fw-1'",
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


def test_provenance_defaults_to_false() -> None:
    decl = ProcessInput.model_validate({"param": "DATASET", "as": "string"})
    assert decl.provenance is False
    assert ProcessInput.model_validate({"as": "string", "provenance": True}).provenance is True


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
    assert spec.provenance_input_names == {"code_revision", "CODE_REVISION", "notes"}
    assert ProcessSpec.model_validate({"name": "none"}).provenance_input_names == set()


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
    with pytest.raises(CLIError, match=r"identity variables changed: DATASET\."):
        _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=tmp_path,
            variables={"RUN_ID": "run-1", "NOTE": "n-1"},
            provenance_names={"NOTE"},
        )


def test_write_run_config_threads_provenance_names_to_validation(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1" / "mine"
    run_dir.mkdir(parents=True)

    def write(revision: str, provenance_names: frozenset[str]) -> None:
        _write_run_config(
            run_dir,
            process_name="mine",
            process_path=Path("process/mine/mine.process.md"),
            run_id="run-1",
            variables={"RUN_ID": "run-1", "CODE_REVISION": revision},
            backend="local",
            variant=None,
            provenance_names=provenance_names,
        )

    write("rev-a", frozenset({"CODE_REVISION"}))
    write("rev-b", frozenset({"CODE_REVISION"}))
    with pytest.raises(CLIError, match=r"identity variables changed: CODE_REVISION\."):
        write("rev-c", frozenset())
