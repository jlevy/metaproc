"""Launch validation reports every class of missing input at once.

Bringing up a cohort used to take one failed launch per class of missing input:
the unresolved ``{{run.dir}}``, then the parameter nobody passed, then the file
that was not there, then the profile the registry did not know. Validation
aggregated within each class but raised on the first non-empty one, so an
operator only ever saw the class that happened to be checked first.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.helpers import load_process_spec
from metaproc.engine.launch_validation import (
    INPUT_FILE_GROUP,
    PARAM_GROUP,
    PLACEHOLDER_GROUP,
    PROFILE_GROUP,
    collect_launch_errors,
    format_launch_errors,
)

RUNNER = CliRunner()

# Four simultaneous defects: an unresolvable template placeholder, an
# operator-supplied parameter nobody passed, a declared input file that is not
# on disk, and a default execution profile no registry defines.
_FOUR_CLASS_SPEC = """\
    ---
    process:
      name: four-class-launch
      defaults:
        default_execution_profile: not-a-real-profile
      inputs:
        ticker: { param: TICKER, as: string }
        roster:
          path: "{{RUNS_DIR}}/absent-roster.yaml"
          parse: { format: yaml }
          as: map
      steps:
        - id: s1
          mode: code
          command: "true"
          outputs:
            out:
              path: "{{MISSING_ROOT}}/s1.txt"
              kind: file
    ---
    # Four-class launch
    """


def _write_spec(tmp_path: Path, body: str) -> Path:
    process_path = tmp_path / "launch.process.md"
    process_path.write_text(textwrap.dedent(body), encoding="utf-8")
    return process_path


def _headers(groups: list[tuple[str, list[str]]]) -> list[str]:
    return [header for header, _messages in groups]


def test_collects_every_class_of_missing_input(tmp_path: Path) -> None:
    process_path = _write_spec(tmp_path, _FOUR_CLASS_SPEC)
    spec = load_process_spec(process_path)

    groups = collect_launch_errors(
        spec,
        {"RUNS_DIR": str(tmp_path)},
        tmp_path,
        process_path=process_path,
    )

    assert _headers(groups) == [PLACEHOLDER_GROUP, PARAM_GROUP, INPUT_FILE_GROUP, PROFILE_GROUP]


def test_one_message_names_every_class(tmp_path: Path) -> None:
    process_path = _write_spec(tmp_path, _FOUR_CLASS_SPEC)
    spec = load_process_spec(process_path)

    message = format_launch_errors(
        collect_launch_errors(
            spec,
            {"RUNS_DIR": str(tmp_path)},
            tmp_path,
            process_path=process_path,
        )
    )

    assert "launch validation failed: 4 problems across 4 classes of input" in message
    # The per-class messages are unchanged; only the grouping is new.
    assert "unresolved placeholder {{MISSING_ROOT}}" in message
    assert "operator-supplied parameter 'TICKER' is not set" in message
    assert "absent-roster.yaml" in message
    assert "unknown execution profile: 'not-a-real-profile'" in message


def test_single_class_reports_without_a_roll_up(tmp_path: Path) -> None:
    """One problem still reads exactly as it always has."""
    process_path = _write_spec(
        tmp_path,
        """\
        ---
        process:
          name: one-class-launch
          steps:
            - id: s1
              mode: code
              command: "true"
              outputs:
                out:
                  path: "{{MISSING_ROOT}}/s1.txt"
                  kind: file
        ---
        # One-class launch
        """,
    )
    spec = load_process_spec(process_path)

    message = format_launch_errors(
        collect_launch_errors(
            spec, {"RUNS_DIR": str(tmp_path)}, tmp_path, process_path=process_path
        )
    )

    assert message.startswith(PLACEHOLDER_GROUP + ":")
    assert "launch validation failed" not in message


def test_valid_launch_collects_nothing(tmp_path: Path) -> None:
    process_path = _write_spec(
        tmp_path,
        """\
        ---
        process:
          name: clean-launch
          inputs:
            ticker: { param: TICKER, as: string }
          steps:
            - id: s1
              mode: code
              command: "true"
              outputs:
                out:
                  path: "{{RUNS_DIR}}/s1.txt"
                  kind: file
        ---
        # Clean launch
        """,
    )
    spec = load_process_spec(process_path)

    groups = collect_launch_errors(
        spec,
        {"RUNS_DIR": str(tmp_path), "TICKER": "AAPL"},
        tmp_path,
        process_path=process_path,
    )

    assert groups == []


def test_unknown_variant_reports_as_an_invalid_execution_profile(tmp_path: Path) -> None:
    process_path = _write_spec(
        tmp_path,
        """\
        ---
        process:
          name: bad-variant-launch
          steps:
            - id: s1
              mode: code
              command: "true"
        ---
        # Bad variant launch
        """,
    )
    spec = load_process_spec(process_path)

    groups = collect_launch_errors(
        spec,
        {"RUNS_DIR": str(tmp_path)},
        tmp_path,
        process_path=process_path,
        adapter_override="no-such-thing",
    )

    assert _headers(groups) == [PROFILE_GROUP]
    assert "unknown adapter override: 'no-such-thing'" in groups[0][1][0]


def test_run_process_dry_run_reports_all_classes_in_one_error(tmp_path: Path) -> None:
    process_path = _write_spec(tmp_path, _FOUR_CLASS_SPEC)

    result = RUNNER.invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={tmp_path}",
            "--var",
            "RUN_ID=dry",
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    error_text = result.output or str(result.exception)
    assert "launch validation failed: 4 problems across 4 classes of input" in error_text
    for header in (PLACEHOLDER_GROUP, PARAM_GROUP, INPUT_FILE_GROUP, PROFILE_GROUP):
        assert header in error_text


def test_run_step_reports_all_classes_in_one_error(tmp_path: Path) -> None:
    process_path = _write_spec(tmp_path, _FOUR_CLASS_SPEC)

    result = RUNNER.invoke(
        app,
        [
            "run-step",
            str(process_path),
            "--step",
            "s1",
            "--var",
            f"RUNS_DIR={tmp_path}",
            "--var",
            "RUN_ID=dry",
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    error_text = result.output or str(result.exception)
    assert "launch validation failed: 4 problems across 4 classes of input" in error_text
    for header in (PLACEHOLDER_GROUP, PARAM_GROUP, INPUT_FILE_GROUP, PROFILE_GROUP):
        assert header in error_text
