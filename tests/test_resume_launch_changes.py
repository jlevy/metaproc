"""A resume records launch-config changes instead of refusing them.

A rerun against one ``RUN_ID`` may change any launch-config field of ``run-config.yaml``:
a resolved variable (through ``--var``, an edited ``default:``, or an input added or
removed), the ``--step-variant`` set, the variant, execution profile, artifact namespace,
and resolved profiles, the backend, and the checkout's ``git_sha``. Each change is
printed as a warning and appended, with the other changes of that resume, as one
``launch_config_change`` event to ``.logs/dispatch-config-changes.jsonl``;
``run-config.yaml`` then records the launch config the resume ran with.
What re-runs follows step fingerprints. A resume refuses only when continuing would
corrupt the run: a corrupt ``run-config.yaml``; a process name other than the recorded
one, since every task record carries ``<process>/<RUN_ID>`` as its run identity; or a
run directory other than the recorded one, since result records are anchored to it.

The end-to-end tests drive ``metaproc run-process`` twice against one ``RUN_ID``: a
launch, then a resume that changes the launch values or edits the process spec.
"""

from __future__ import annotations

import json
import logging
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from strif import atomic_write_text
from typer.testing import CliRunner, Result

from metaproc.cli import app
from metaproc.commands import run_process
from metaproc.commands.helpers import load_process_spec
from metaproc.commands.run_process import _LaunchConfig, _validate_run_config
from metaproc.engine.build_plan import build_plan
from metaproc.engine.dep_state import fingerprint_step
from metaproc.engine.process_scope import expand_process_vars
from metaproc.errors import CLIError
from metaproc.io import iter_jsonl_objects, read_yaml_file, to_yaml_string
from metaproc.io.orchestrator_lease import acquire_lease, release_lease
from metaproc.paths import (
    ORCHESTRATOR_LEASE_FILE,
    RUN_CONFIG_FILE,
    STATE_DIR,
    dispatch_config_changes_log,
)

_HANDLERS = '''\
"""Code handlers for the resume launch-change tests."""

from __future__ import annotations

from pathlib import Path


def _log(variables: dict[str, str], entry: str) -> Path:
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "invocations.log").open("a", encoding="utf-8") as fh:
        fh.write(entry + "\\n")
    return run_dir


def record(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    run_dir = _log(variables, "record")
    (run_dir / "record.txt").write_text(variables["DATASET"] + "\\n", encoding="utf-8")


def stamp(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    run_dir = _log(variables, "stamp")
    (run_dir / "stamp.txt").write_text(variables["DATASET"] + "\\n", encoding="utf-8")
'''

_PROCESS_NAME = "launch-changes"


def _write_process(
    process_dir: Path,
    *,
    name: str = _PROCESS_NAME,
    file_name: str = "launch-changes.process.md",
    mode_default: str | None = "mode-a",
    extra_inputs: str = "",
) -> Path:
    """Write the test process: one required input, one optional input, two code steps.

    ``record`` reads ``DATASET`` only at runtime, so its fingerprint does not depend on
    the value; ``stamp`` binds it into ``env:``, so its fingerprint does. ``mode`` is
    optional with a literal ``default:``, so editing the default changes the resolved
    set with no ``--var`` changing; ``mode_default=None`` leaves the input out.
    """
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "handlers.py").write_text(_HANDLERS, encoding="utf-8")
    inputs = textwrap.dedent(
        """\
        dataset:
          param: DATASET
          as: string
        """
    )
    if mode_default is not None:
        inputs += textwrap.dedent(
            f"""\
            mode:
              param: MODE
              as: string
              required: false
              default: {mode_default}
            """
        )
    inputs += textwrap.dedent(extra_inputs)
    steps = textwrap.dedent(
        """\
        steps:
          - id: record
            mode: code
            handler: "handlers.py:record"
            outputs:
              out:
                path: "{{run.dir}}/record.txt"
                kind: file
          - id: stamp
            mode: code
            handler: "handlers.py:stamp"
            env:
              STAMP_DATASET: "{{DATASET}}"
            outputs:
              out:
                path: "{{run.dir}}/stamp.txt"
                kind: file
        """
    )
    spec = (
        "---\n"
        "process:\n"
        f"  name: {name}\n"
        "  inputs:\n"
        + textwrap.indent(inputs, "    ")
        + textwrap.indent(steps, "  ")
        + "---\n# Launch Changes Fixture\n"
    )
    path = process_dir / file_name
    path.write_text(spec, encoding="utf-8")
    return path


def _run(
    process_path: Path, runs_dir: Path, run_id: str, *options: str, **variables: str
) -> Result:
    args = [
        "run-process",
        str(process_path),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        f"RUN_ID={run_id}",
        "--backend",
        "local",
        *options,
    ]
    for name, value in variables.items():
        args.extend(["--var", f"{name}={value}"])
    return CliRunner().invoke(app, args)


def _message(result: Result) -> str:
    return f"{result.output}\n{result.exception}"


def _invocations(run_dir: Path) -> list[str]:
    return (run_dir / "invocations.log").read_text(encoding="utf-8").splitlines()


def _config(run_dir: Path) -> dict[str, object]:
    return read_yaml_file(run_dir / STATE_DIR / RUN_CONFIG_FILE)


def _events(run_dir: Path) -> list[dict[str, Any]]:
    path = dispatch_config_changes_log(run_dir)
    return list(iter_jsonl_objects(path)) if path.is_file() else []


def _launch(tmp_path: Path) -> tuple[Path, Path, str]:
    process_path = _write_process(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "launch-run"
    launched = _run(process_path, runs_dir, run_id, DATASET="ds-1")
    assert launched.exit_code == 0, _message(launched)
    assert sorted(_invocations(runs_dir / run_id)) == ["record", "stamp"]
    return process_path, runs_dir, run_id


_SUMMARIES = ("operations-summary.md", "resource-usage-summary.md")
"""The run summaries finalization writes; a refused resume must leave both alone."""


def _diff(field: str, old: str | None, new: str | None) -> dict[str, object]:
    return {"field": field, "diff": {"old": old, "new": new}}


# ── End to end through run-process ────────────────────────────────


def test_a_changed_variable_resumes_and_is_recorded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id

    with caplog.at_level(logging.INFO, logger="metaproc.commands.run_process"):
        resumed = _run(process_path, runs_dir, run_id, DATASET="ds-2")

    assert resumed.exit_code == 0, _message(resumed)
    # The operator sees each change, under both names resolution writes, and where the
    # run recorded them.
    assert "Warning: Resume changes variables.DATASET: 'ds-1' -> 'ds-2'" in resumed.output
    assert "Warning: Resume changes variables.dataset: 'ds-1' -> 'ds-2'" in resumed.output
    assert "Recorded 2 launch-config change(s) as a launch_config_change event in " in (
        resumed.output
    )
    assert str(dispatch_config_changes_log(run_dir)) in resumed.output
    # The log keeps each change at INFO, so the operator's warning is the only one.
    logged = [
        (r.levelno, r.getMessage())
        for r in caplog.records
        if r.getMessage().startswith("Resume changes ")
    ]
    assert logged == [
        (logging.INFO, "Resume changes variables.DATASET: 'ds-1' -> 'ds-2'"),
        (logging.INFO, "Resume changes variables.dataset: 'ds-1' -> 'ds-2'"),
    ]
    # One event holds every change of the resume.
    events = _events(run_dir)
    assert [event["event"] for event in events] == ["launch_config_change"]
    assert events[0]["changes"] == [
        _diff("variables.DATASET", "ds-1", "ds-2"),
        _diff("variables.dataset", "ds-1", "ds-2"),
    ]
    # The config now records the value the resume ran with.
    variables = _config(run_dir)["variables"]
    assert isinstance(variables, dict)
    assert variables["DATASET"] == "ds-2"
    assert variables["dataset"] == "ds-2"
    # Reuse follows fingerprints: ``record`` does not depend on the value and is reused,
    # ``stamp`` binds it into ``env:`` and re-runs.
    invocations = _invocations(run_dir)
    assert invocations.count("record") == 1
    assert invocations.count("stamp") == 2


def test_an_unwritable_change_log_refuses_the_resume_as_a_cli_error(tmp_path: Path) -> None:
    """A change log the resume cannot append to fails with its path, not a traceback,
    and leaves the config holding the values the resume would have replaced.

    The refusal comes under the lease but before anything runs, so the run is not
    finalized again: its summaries still describe the completed launch.
    """
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id
    summaries = {name: (run_dir / name).read_bytes() for name in _SUMMARIES}
    changes_log = dispatch_config_changes_log(run_dir)
    # A directory where the log belongs cannot be opened for append.
    changes_log.mkdir(parents=True)

    refused = _run(process_path, runs_dir, run_id, DATASET="ds-2")

    assert refused.exit_code != 0
    assert isinstance(refused.exception, CLIError), _message(refused)
    assert str(changes_log) in str(refused.exception)
    assert isinstance(refused.exception.__cause__, OSError)
    assert "Traceback" not in refused.output
    variables = _config(run_dir)["variables"]
    assert isinstance(variables, dict)
    assert variables["DATASET"] == "ds-1"
    assert sorted(_invocations(run_dir)) == ["record", "stamp"]
    assert {name: (run_dir / name).read_bytes() for name in _SUMMARIES} == summaries
    # The refused resume released its lease.
    assert not (run_dir / STATE_DIR / ORCHESTRATOR_LEASE_FILE).exists()


def test_an_edited_default_resumes_and_is_recorded(tmp_path: Path) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id
    _write_process(process_path.parent, mode_default="mode-b")

    resumed = _run(process_path, runs_dir, run_id, DATASET="ds-1")

    assert resumed.exit_code == 0, _message(resumed)
    assert "Warning: Resume changes variables.MODE: 'mode-a' -> 'mode-b'" in resumed.output
    assert [event["changes"] for event in _events(run_dir)] == [
        [
            _diff("variables.MODE", "mode-a", "mode-b"),
            _diff("variables.mode", "mode-a", "mode-b"),
        ]
    ]
    variables = _config(run_dir)["variables"]
    assert isinstance(variables, dict)
    assert (variables["MODE"], variables["mode"]) == ("mode-b", "mode-b")
    # No step's fingerprint reads the mode, so nothing re-runs.
    assert sorted(_invocations(run_dir)) == ["record", "stamp"]


def test_a_newly_added_variable_resumes_and_is_recorded(tmp_path: Path) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id
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

    resumed = _run(process_path, runs_dir, run_id, DATASET="ds-1")

    assert resumed.exit_code == 0, _message(resumed)
    assert "Warning: Resume changes variables.REGION: <unset> -> 'us-east'" in resumed.output
    assert [event["changes"] for event in _events(run_dir)] == [
        [
            _diff("variables.REGION", None, "us-east"),
            _diff("variables.region", None, "us-east"),
        ]
    ]
    variables = _config(run_dir)["variables"]
    assert isinstance(variables, dict)
    assert variables["REGION"] == "us-east"
    assert sorted(_invocations(run_dir)) == ["record", "stamp"]


def test_a_removed_variable_resumes_and_is_recorded(tmp_path: Path) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id
    _write_process(process_path.parent, mode_default=None)

    resumed = _run(process_path, runs_dir, run_id, DATASET="ds-1")

    assert resumed.exit_code == 0, _message(resumed)
    assert "Warning: Resume changes variables.MODE: 'mode-a' -> <unset>" in resumed.output
    assert [event["changes"] for event in _events(run_dir)] == [
        [
            _diff("variables.MODE", "mode-a", None),
            _diff("variables.mode", "mode-a", None),
        ]
    ]
    variables = _config(run_dir)["variables"]
    assert isinstance(variables, dict)
    assert "MODE" not in variables and "mode" not in variables
    assert sorted(_invocations(run_dir)) == ["record", "stamp"]


def test_a_resume_with_the_launch_values_records_and_rewrites_nothing(tmp_path: Path) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id
    config_bytes = (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes()

    resumed = _run(process_path, runs_dir, run_id, DATASET="ds-1")

    assert resumed.exit_code == 0, _message(resumed)
    assert "Resume changes" not in resumed.output
    assert _events(run_dir) == []
    assert (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes() == config_bytes
    assert sorted(_invocations(run_dir)) == ["record", "stamp"]


def test_a_changed_process_name_still_refuses(tmp_path: Path) -> None:
    """Every task record carries ``<process>/<RUN_ID>``, so a rename cannot read them.

    The refusal comes before anything is recorded or rewritten, and names both ways out.
    """
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id
    config_bytes = (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes()
    renamed = _write_process(
        process_path.parent, name="launch-changes-renamed", file_name="renamed.process.md"
    )

    refused = _run(renamed, runs_dir, run_id, DATASET="ds-2")

    assert refused.exit_code != 0
    message = _message(refused)
    assert "Resume refused: run-config.yaml records process 'launch-changes'" in message
    assert f"run identity {_PROCESS_NAME}/{run_id}" in message
    assert f"Resume with the process spec named {_PROCESS_NAME!r}" in message
    assert "or use a new RUN_ID" in message
    assert "Resume changes" not in message
    assert _events(run_dir) == []
    assert (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes() == config_bytes
    assert sorted(_invocations(run_dir)) == ["record", "stamp"]


def test_a_corrupt_run_config_still_refuses(tmp_path: Path) -> None:
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id
    (run_dir / STATE_DIR / RUN_CONFIG_FILE).write_text("process: [unterminated\n", encoding="utf-8")

    refused = _run(process_path, runs_dir, run_id, DATASET="ds-1")

    assert refused.exit_code != 0
    assert "Corrupt run-config.yaml" in _message(refused)
    assert sorted(_invocations(run_dir)) == ["record", "stamp"]
    assert _events(run_dir) == []


def test_a_moved_run_directory_refuses_and_leaves_the_run_as_it_was(tmp_path: Path) -> None:
    """Result records are anchored to the recorded run directory, so a move refuses.

    Resuming here would re-run both steps, whose outputs sit under ``{{run.dir}}``, and
    write result records the projection cannot accept. The refusal names both
    directories and both ways out, before anything is recorded, rewritten, or run.
    """
    process_path = _write_process(tmp_path / "proc")
    run_id = "moved-run"
    original = tmp_path / "runs-a" / run_id
    launched = _run(process_path, tmp_path / "runs-a", run_id, DATASET="ds-1")
    assert launched.exit_code == 0, _message(launched)
    moved = tmp_path / "runs-b" / run_id
    moved.parent.mkdir()
    original.rename(moved)
    config_bytes = (moved / STATE_DIR / RUN_CONFIG_FILE).read_bytes()
    summaries = {name: (moved / name).read_bytes() for name in _SUMMARIES}

    refused = _run(process_path, tmp_path / "runs-b", run_id, DATASET="ds-1")

    assert refused.exit_code != 0
    assert isinstance(refused.exception, CLIError), _message(refused)
    message = str(refused.exception)
    assert f"records run directory {str(original)!r}" in message
    assert f"this launch resolves to {str(moved)!r}" in message
    assert f"Resume at {str(original)!r}" in message
    assert "or start a new RUN_ID" in message
    assert "Resume changes" not in refused.output
    assert (moved / STATE_DIR / RUN_CONFIG_FILE).read_bytes() == config_bytes
    assert _events(moved) == []
    assert {name: (moved / name).read_bytes() for name in _SUMMARIES} == summaries
    assert sorted(_invocations(moved)) == ["record", "stamp"]


def _step_states(run_dir: Path) -> dict[str, str]:
    """Return each step's state as ``metaproc status --steps`` reports it."""
    status = CliRunner().invoke(app, ["status", str(run_dir), "--steps", "--format", "json"])
    assert status.exit_code == 0, _message(status)
    return {step["step_id"]: step["state"] for step in json.loads(status.output)["steps"]}


def test_a_changed_artifact_namespace_is_recorded_and_the_config_describes_the_resume(
    tmp_path: Path,
) -> None:
    """Every reader rebuilds from the rewritten config, so re-run steps read current."""
    process_path = _write_process(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "namespace-run"
    run_dir = runs_dir / run_id
    launched = _run(process_path, runs_dir, run_id, "--artifact-namespace", "ns-a", DATASET="ds-1")
    assert launched.exit_code == 0, _message(launched)

    resumed = _run(process_path, runs_dir, run_id, "--artifact-namespace", "ns-b", DATASET="ds-1")

    assert resumed.exit_code == 0, _message(resumed)
    assert "Warning: Resume changes artifact_namespace: 'ns-a' -> 'ns-b'" in resumed.output
    assert [event["changes"] for event in _events(run_dir)] == [
        [
            _diff("artifact_namespace", "ns-a", "ns-b"),
            _diff("variables.ARTIFACT_NAMESPACE", "ns-a", "ns-b"),
            _diff("variables.VARIANT", "ns-a", "ns-b"),
            _diff("variant", "ns-a", "ns-b"),
        ]
    ]
    config = _config(run_dir)
    assert (config["artifact_namespace"], config["variant"]) == ("ns-b", "ns-b")
    variables = config["variables"]
    assert isinstance(variables, dict)
    assert variables["ARTIFACT_NAMESPACE"] == "ns-b"
    # The namespace is in both fingerprints, so both steps re-ran, and status, which
    # rebuilds the plan from the config, reports them current rather than stale.
    assert sorted(_invocations(run_dir)) == ["record", "record", "stamp", "stamp"]
    assert _step_states(run_dir) == {"record": "current", "stamp": "current"}


def test_a_changed_variant_is_recorded_and_the_operations_summary_names_it(
    tmp_path: Path,
) -> None:
    """The resolved profiles are recorded whole in the event and by name in the warning."""
    spec, profiles = _write_profiled_process(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "variant-run"
    run_dir = runs_dir / run_id
    profile_file = ("--profile-file", str(profiles))
    launched = _run(spec, runs_dir, run_id, *profile_file, "--variant", "run-a")
    assert launched.exit_code == 0, _message(launched)

    resumed = _run(spec, runs_dir, run_id, *profile_file, "--variant", "judge-b")

    assert resumed.exit_code == 0, _message(resumed)
    assert "Warning: Resume changes variant: 'run-a' -> 'judge-b'" in resumed.output
    assert "Warning: Resume changes execution_profile: 'run-a' -> 'judge-b'" in resumed.output
    assert "Warning: Resume changes resolved_profiles: ['run-a'] -> ['judge-b']" in resumed.output
    (event,) = _events(run_dir)
    changes = {change["field"]: change["diff"] for change in event["changes"]}
    assert changes["variant"] == {"old": "run-a", "new": "judge-b"}
    assert changes["execution_profile"] == {"old": "run-a", "new": "judge-b"}
    profile_diff = changes["resolved_profiles"]
    assert isinstance(profile_diff, dict)
    assert [profile["name"] for profile in profile_diff["old"]] == ["run-a"]
    assert [profile["name"] for profile in profile_diff["new"]] == ["judge-b"]
    assert profile_diff["new"][0]["adapter"] == "claude-code-cli"
    config = _config(run_dir)
    assert (config["variant"], config["execution_profile"]) == ("judge-b", "judge-b")
    assert config["resolved_profiles"] == profile_diff["new"]
    summary = (run_dir / "operations-summary.md").read_text(encoding="utf-8")
    assert "| Variant | judge-b |" in summary
    assert "| Execution profile | judge-b |" in summary
    assert sorted(_invocations(run_dir)) == ["draft", "draft", "judge", "judge"]


def test_a_resume_from_another_checkout_records_its_git_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process_path = _write_process(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "checkout-run"
    run_dir = runs_dir / run_id
    monkeypatch.setattr(run_process, "_get_git_sha", lambda: "1111111")
    launched = _run(process_path, runs_dir, run_id, DATASET="ds-1")
    assert launched.exit_code == 0, _message(launched)
    assert _config(run_dir)["git_sha"] == "1111111"

    monkeypatch.setattr(run_process, "_get_git_sha", lambda: "2222222")
    resumed = _run(process_path, runs_dir, run_id, DATASET="ds-1")

    assert resumed.exit_code == 0, _message(resumed)
    assert "Warning: Resume changes git_sha: '1111111' -> '2222222'" in resumed.output
    assert [event["changes"] for event in _events(run_dir)] == [
        [_diff("git_sha", "1111111", "2222222")]
    ]
    assert _config(run_dir)["git_sha"] == "2222222"
    # No step's fingerprint reads the checkout, so nothing re-runs.
    assert sorted(_invocations(run_dir)) == ["record", "stamp"]


# ── The --step-variant set ─────────────────────────────────────────


def _write_profiled_process(process_dir: Path) -> tuple[Path, Path]:
    """Write a two-step process whose steps log each run.

    Returns the spec and an execution-profile file declaring ``run-a`` and ``judge-b``.
    A step's resolved profile is part of its fingerprint, so a step whose override
    changes re-runs and the other is reused.
    """
    process_dir.mkdir(parents=True, exist_ok=True)
    profiles = process_dir / "execution-profiles.yaml"
    profiles.write_text(
        json.dumps(
            {
                "profiles": {
                    name: {"adapter": "claude-code-cli", "status": "tested"}
                    for name in ("run-a", "judge-b")
                }
            }
        ),
        encoding="utf-8",
    )

    def step(step_id: str) -> str:
        return textwrap.dedent(
            f"""\
            - id: {step_id}
              mode: code
              command: >-
                /bin/sh -c 'mkdir -p "{{{{run.dir}}}}";
                echo "{step_id}" >> "{{{{run.dir}}}}/invocations.log";
                echo "{step_id}" > "{{{{run.dir}}}}/{step_id}.txt"'
              outputs:
                out:
                  path: "{{{{run.dir}}}}/{step_id}.txt"
                  kind: file
            """
        )

    spec = process_dir / "judged.process.md"
    spec.write_text(
        "---\nprocess:\n  name: judged\n  steps:\n"
        + textwrap.indent(step("draft") + step("judge"), "    ")
        + "---\n# Judged Fixture\n",
        encoding="utf-8",
    )
    return spec, profiles


def test_a_different_step_variant_set_resumes_and_is_recorded(tmp_path: Path) -> None:
    spec, profiles = _write_profiled_process(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "judged-run"
    run_dir = runs_dir / run_id
    base = ("--profile-file", str(profiles), "--variant", "run-a")

    launched = _run(spec, runs_dir, run_id, *base, "--step-variant", "judge=judge-b")
    assert launched.exit_code == 0, _message(launched)
    assert _config(run_dir)["step_variants"] == {"judge": "judge-b"}
    assert sorted(_invocations(run_dir)) == ["draft", "judge"]

    # A resume without --step-variant re-applies the recorded set: nothing changes, so
    # both steps keep their fingerprints and are reused.
    reapplied = _run(spec, runs_dir, run_id, *base)
    assert reapplied.exit_code == 0, _message(reapplied)
    assert "Resume changes" not in reapplied.output
    assert _events(run_dir) == []
    assert sorted(_invocations(run_dir)) == ["draft", "judge"]

    # A different set runs as passed, and the change is recorded.
    changed = _run(spec, runs_dir, run_id, *base, "--step-variant", "judge=run-a")
    assert changed.exit_code == 0, _message(changed)
    assert "Warning: Resume changes step_variants.judge: 'judge-b' -> 'run-a'" in changed.output
    # The plan no longer uses ``judge-b``, so its resolved profiles change with the set.
    assert "Resume changes resolved_profiles: ['judge-b', 'run-a'] -> ['run-a']" in changed.output
    (event,) = _events(run_dir)
    assert [change["field"] for change in event["changes"]] == [
        "resolved_profiles",
        "step_variants.judge",
    ]
    assert event["changes"][1] == _diff("step_variants.judge", "judge-b", "run-a")
    assert _config(run_dir)["step_variants"] == {"judge": "run-a"}
    # Only the step whose profile changed re-runs.
    assert sorted(_invocations(run_dir)) == ["draft", "judge", "judge"]

    # The next resume without --step-variant re-applies the newly recorded set.
    again = _run(spec, runs_dir, run_id, *base)
    assert again.exit_code == 0, _message(again)
    assert len(_events(run_dir)) == 1
    assert sorted(_invocations(run_dir)) == ["draft", "judge", "judge"]


def test_a_changed_variable_changes_only_the_fingerprints_that_bind_it(tmp_path: Path) -> None:
    """Recording a change does not decide what re-runs; step fingerprints do.

    A value bound through a composite step's ``with:`` stays a template in the resolved
    plan, so changing it leaves that step's fingerprint alone. A value substituted into a
    resolved field such as ``env:`` is in the payload, so the step re-runs with its
    downstream on the next resume. Absolute fingerprint values are not asserted: a
    composite step's payload carries its absolute ``uses_path``, so its hash depends on
    where the process files live.
    """
    (tmp_path / "child.process.md").write_text(
        textwrap.dedent(
            """\
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
            """
        ),
        encoding="utf-8",
    )
    process_path = tmp_path / "parent.process.md"
    process_path.write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: parent
              inputs:
                code_revision: {param: CODE_REVISION, as: string}
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
            """
        ),
        encoding="utf-8",
    )
    spec = load_process_spec(process_path)

    def fingerprints(revision: str) -> dict[str, str]:
        params = expand_process_vars(
            spec,
            {"CODE_REVISION": revision, "RUN_ID": "run-1", "RUNS_DIR": str(tmp_path / "runs")},
        )
        plan = build_plan(spec, params, process_path=process_path)
        return {step.step_id: fingerprint_step(step) for step in plan.steps}

    before, after = fingerprints("rev-a"), fingerprints("rev-b")

    assert before["via-with"] == after["via-with"]
    assert before["via-env"] != after["via-env"]


# ── Nothing is recorded before the orchestrator lease ──────────────


def test_a_resume_refused_by_a_live_lease_records_nothing(tmp_path: Path) -> None:
    """Another orchestrator may be executing the run; its config and log stay untouched."""
    process_path, runs_dir, run_id = _launch(tmp_path)
    run_dir = runs_dir / run_id
    config_bytes = (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes()

    acquire_lease(run_dir, command_summary="live orchestrator")
    try:
        refused = _run(process_path, runs_dir, run_id, DATASET="ds-2")
    finally:
        release_lease(run_dir)

    assert refused.exit_code != 0
    assert "Another orchestrator holds the lease" in _message(refused)
    # The operator is not told of changes that were never recorded.
    assert "Resume changes" not in refused.output
    assert (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes() == config_bytes
    assert _events(run_dir) == []
    assert sorted(_invocations(run_dir)) == ["record", "stamp"]


def _write_chain_process(process_dir: Path) -> Path:
    """Write a two-step process in which ``second`` needs ``first``."""
    process_dir.mkdir(parents=True, exist_ok=True)
    spec = process_dir / "chain.process.md"
    spec.write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: chain
              inputs:
                dataset:
                  param: DATASET
                  as: string
                  required: false
                  default: ds-1
              steps:
                - id: first
                  mode: code
                  command: /bin/sh -c 'mkdir -p "{{run.dir}}"; echo first > "{{run.dir}}/first.txt"'
                  outputs:
                    out:
                      path: "{{run.dir}}/first.txt"
                      kind: file
                - id: second
                  mode: code
                  needs: [first]
                  command: /bin/sh -c 'mkdir -p "{{run.dir}}"; echo second > "{{run.dir}}/second.txt"'
                  outputs:
                    out:
                      path: "{{run.dir}}/second.txt"
                      kind: file
            ---
            # Chain Fixture
            """
        ),
        encoding="utf-8",
    )
    return spec


def test_a_resume_refused_for_unsatisfied_ancestors_records_nothing(tmp_path: Path) -> None:
    spec = _write_chain_process(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "chain-run"
    # ``--force`` skips the ancestor check, so ``second`` runs and ``first`` never does.
    launched = _run(spec, runs_dir, "chain-run", "--only", "second", "--force")
    assert launched.exit_code == 0, _message(launched)
    config_bytes = (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes()

    refused = _run(spec, runs_dir, "chain-run", "--from", "second", DATASET="ds-2")

    assert refused.exit_code != 0
    assert "Unsatisfied ancestors for --from second" in _message(refused)
    assert "Resume changes" not in refused.output
    assert (run_dir / STATE_DIR / RUN_CONFIG_FILE).read_bytes() == config_bytes
    assert _events(run_dir) == []


def test_an_unsatisfied_ancestor_refusal_names_the_override(tmp_path: Path) -> None:
    spec = _write_chain_process(tmp_path / "proc")

    refused = _run(spec, tmp_path / "runs", "chain-run", "--only", "second")

    assert refused.exit_code != 0
    message = _message(refused)
    assert "Unsatisfied ancestors for --only second" in message
    assert (
        "or use --force, or record an override with: "
        "metaproc override <RUN_ID> <step> --process <spec> --satisfied" in message
    )


# ── The comparison directly ────────────────────────────────────────


def _write_config(config_dir: Path, data: dict[str, object]) -> Path:
    config_path = config_dir / STATE_DIR / RUN_CONFIG_FILE
    atomic_write_text(config_path, to_yaml_string(data), make_parents=True)
    return config_path


def _launch_config(
    variables: dict[str, str],
    *,
    step_variants: dict[str, str] | None = None,
    backend: str = "",
) -> _LaunchConfig:
    """The launch config of a config that records only these fields.

    An empty value is absent, as in the hand-written configs these tests compare.
    """
    return _LaunchConfig(
        variables=variables,
        step_variants=step_variants or {},
        variant=None,
        execution_profile=None,
        artifact_namespace=None,
        resolved_profiles=[],
        backend=backend,
        git_sha="",
    )


def test_runs_dir_filestore_aliases_record_no_change(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        {
            "process": "mine",
            "run_dir": "/mnt/disks/filestore/runs/run-1",
            "variables": {"RUNS_DIR": "/mnt/disks/filestore/runs", "RUN_ID": "run-1"},
        },
    )

    changes = _validate_run_config(
        config_path,
        process_name="mine",
        run_dir=Path("/mnt/filestore/runs/run-1"),
        launch=_launch_config({"RUNS_DIR": "/mnt/filestore/runs", "RUN_ID": "run-1"}),
    )

    assert changes == {}


def test_a_workstation_path_containing_a_filestore_alias_refuses(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        {
            "process": "mine",
            "run_dir": "/mnt/filestore/runs/run-1",
            "variables": {"RUNS_DIR": "/mnt/filestore/runs"},
        },
    )

    with pytest.raises(CLIError, match=r"records run directory '/mnt/filestore/runs/run-1'"):
        _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=Path("/workspace/user/mnt/filestore/runs/run-1"),
            launch=_launch_config({"RUNS_DIR": "/workspace/user/mnt/filestore/runs"}),
        )


def test_changes_are_returned_sorted_by_field(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        {
            "process": "mine",
            "run_dir": str(tmp_path),
            "variables": {"ZONE": "z", "BETA": "b-1", "ALPHA": "a-1"},
            "step_variants": {"judge": "judge-a"},
            "backend": "local",
        },
    )

    changes = _validate_run_config(
        config_path,
        process_name="mine",
        run_dir=tmp_path,
        launch=_launch_config(
            {"ZONE": "z", "ALPHA": "a-2", "GAMMA": "g-1"},
            step_variants={"draft": "draft-b", "judge": "judge-a"},
            backend="gcp-worker",
        ),
    )

    # Whole fields and keyed ones sort together, by field.
    assert list(changes.items()) == [
        ("backend", ("local", "gcp-worker")),
        ("step_variants.draft", (None, "draft-b")),
        ("variables.ALPHA", ("a-1", "a-2")),
        ("variables.BETA", ("b-1", None)),
        ("variables.GAMMA", (None, "g-1")),
    ]


def test_an_empty_step_variant_set_is_a_change_from_a_recorded_one(tmp_path: Path) -> None:
    """A resume always states its set; ``_resume_step_variants`` re-applies the record."""
    config_path = _write_config(
        tmp_path,
        {
            "process": "mine",
            "run_dir": str(tmp_path),
            "step_variants": {"judge": "judge-a"},
        },
    )

    def compare(step_variants: dict[str, str]) -> Mapping[str, tuple[object, object]]:
        return _validate_run_config(
            config_path,
            process_name="mine",
            run_dir=tmp_path,
            launch=_launch_config({}, step_variants=step_variants),
        )

    assert compare({"judge": "judge-a"}) == {}
    assert compare({}) == {"step_variants.judge": ("judge-a", None)}


def test_a_process_name_mismatch_raises(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path, {"process": "mine", "run_id": "run-1", "run_dir": str(tmp_path)}
    )

    with pytest.raises(CLIError, match=r"run identity mine/run-1") as exc_info:
        _validate_run_config(
            config_path, process_name="theirs", run_dir=tmp_path, launch=_launch_config({})
        )
    assert "Resume with the process spec named 'mine', or use a new RUN_ID" in str(exc_info.value)


@pytest.mark.parametrize(
    "text",
    [
        "process: [unterminated\n",
        "- a list\n",
        "run_id: run-1\n",
        "process: mine\nvariables:\n  COUNT: 3\n",
    ],
    ids=["unparsable", "not-a-mapping", "no-process", "non-string-variables"],
)
def test_a_corrupt_config_refuses(tmp_path: Path, text: str) -> None:
    config_path = tmp_path / STATE_DIR / RUN_CONFIG_FILE
    config_path.parent.mkdir(parents=True)
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(CLIError, match=r"Corrupt run-config\.yaml"):
        _validate_run_config(
            config_path, process_name="mine", run_dir=tmp_path, launch=_launch_config({})
        )
