"""Expected executor errors remain durable across process and task boundaries."""

from __future__ import annotations

import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands import run_process as run_process_module
from metaproc.engine.fan_in import collect_item_outcomes
from metaproc.engine.input_validation import validate_process_outputs_detailed
from metaproc.engine.run_status import scan_run_status
from metaproc.io import read_yaml_file
from metaproc.io.state_io import read_attempt_history_at, read_status_at
from metaproc.models.authored import ProcessSpec


@pytest.mark.parametrize("stream", ["stderr", "stdout"])
def test_command_diagnostic_reaches_attempt_mapped_item_and_root_failure(
    tmp_path: Path, stream: str
) -> None:
    diagnostic = 'RuntimeError: query terms must not contain commas: "Example, Inc."'
    (tmp_path / "fail.py").write_text(
        "import sys\n"
        "print('provider request started')\n"
        f"print({diagnostic!r}, file=sys.{stream})\n"
        "raise SystemExit(7)\n"
    )
    (tmp_path / "child.process.md").write_text(
        "---\nprocess:\n  name: child\n  steps:\n"
        "    - id: query\n      mode: code\n"
        f"      command: '{sys.executable} fail.py'\n---\n"
    )
    (tmp_path / "items.md").write_text("---\nprogress:\n  items:\n    - item: alfa\n---\n")
    process = tmp_path / "parent.process.md"
    process.write_text(
        textwrap.dedent("""\
        ---
        process:
          name: parent
          deps:
            child: {path: ./child.process.md, as: path}
            items: {path: ./items.md, as: path}
          steps:
            - id: child
              mode: composite
              uses: deps.child
              for_each:
                over: deps.items
                bind: item
                bind_fields: [item]
                key: "{{item}}"
        ---
        """)
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=command",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "command"
    leaf = run / "child" / "alfa" / ".state" / "tasks" / "query"
    attempt = read_attempt_history_at(leaf)[0]
    assert attempt.error and diagnostic in attempt.error
    assert "command exit code 7" in attempt.error
    assert diagnostic in collect_item_outcomes(run, "child")[0]["error"]
    root = scan_run_status(run, include_system=False)
    assert root.process_error and diagnostic in root.process_error
    assert "command exit code 7" in root.process_error


@pytest.mark.parametrize("mapped", [False, True])
def test_command_diagnostic_is_bounded_redacted_and_prefers_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mapped: bool
) -> None:
    secret = "fixture-private-value-" + "s" * 300
    monkeypatch.setenv("PROVIDER_API_KEY", secret)
    diagnostic = "ProviderError: HTTP 429 Too Many Requests; Retry-After: 17"
    (tmp_path / "fail.py").write_text(
        "import os, sys\n"
        'print(\'{"type":"result","status":"success"}\')\n'
        "print('unrelated progress\\n' * 500, file=sys.stderr)\n"
        f"print({diagnostic!r}, file=sys.stderr)\n"
        "print('credential=' + os.environ['PROVIDER_API_KEY'], file=sys.stderr)\n"
        "print('unlabeled ' + os.environ['PROVIDER_API_KEY'].replace('private', '\\x1b[31mprivate\\x1b[0m'), file=sys.stderr)\n"
        "print('Authorization: Bearer fixture-bearer-token', file=sys.stderr)\n"
        "raise SystemExit(9)\n"
    )
    (tmp_path / "items.md").write_text("---\nprogress:\n  items:\n    - item: alfa\n---\n")
    process = tmp_path / "command.process.md"
    process.write_text(
        "---\nprocess:\n  name: command\n"
        "  deps:\n    items: {path: ./items.md, as: path}\n"
        "  steps:\n    - id: query\n      mode: code\n"
        f"      command: '{sys.executable} fail.py'\n"
        + (
            "      for_each:\n        over: deps.items\n        bind: item\n"
            "        bind_fields: [item]\n        key: '{{item}}'\n"
            if mapped
            else ""
        )
        + "---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=redacted",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "redacted"
    task = run / ".state" / "tasks" / "query"
    if mapped:
        task /= "alfa"
    attempt = read_attempt_history_at(task)[0]
    assert attempt.error and diagnostic in attempt.error
    assert "command exit code 9" in attempt.error
    assert attempt.failure_class == "rate_limited"
    assert len(attempt.error) < 2_000
    assert "[redacted]" in attempt.error
    root = scan_run_status(run, include_system=False)
    assert root.process_error and diagnostic in root.process_error
    assert "fixture-private-value" not in root.process_error
    assert "fixture-bearer-token" not in root.process_error
    assert '"status":"success"' not in root.process_error
    logs = run / ".logs" / "tasks" / "query"
    if mapped:
        logs /= "alfa"
    captured = next(logs.glob("process_*.log")).read_text()
    assert secret in captured  # Source evidence is retained; the durable summary is redacted.
    assert "unrelated progress" in captured


@pytest.mark.parametrize("step_id,second", [("quota-check", 29), ("query", 29), ("query", 3)])
def test_command_failure_class_ignores_step_names_and_log_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step_id: str, second: int
) -> None:
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime(2026, 9, 13, 0, 44 if second == 29 else 5, second, tzinfo=UTC)
    monkeypatch.setattr(run_process_module, "datetime", clock)
    (tmp_path / "fail.py").write_text(
        "import sys\nprint('ValueError: invalid input', file=sys.stderr)\nraise SystemExit(1)\n"
    )
    process = tmp_path / "command.process.md"
    process.write_text(
        "---\nprocess:\n  name: command\n  steps:\n"
        f"    - id: {step_id}\n      mode: code\n"
        f"      command: '{sys.executable} fail.py'\n---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=classification",
        ],
    )
    assert result.exit_code == 1, result.output
    task = tmp_path / "runs" / "classification" / ".state" / "tasks" / step_id
    attempt = read_attempt_history_at(task)[0]
    assert attempt.error and "ValueError: invalid input" in attempt.error
    assert attempt.failure_class == "crash"


@pytest.mark.parametrize("mapped", [False, True])
def test_composite_output_failure_retains_path_message_and_failed_scope(
    tmp_path: Path,
    mapped: bool,
) -> None:
    (tmp_path / "child.process.md").write_text(
        textwrap.dedent("""\
        ---
        process:
          name: child
          outputs:
            report: {path: "{{run.dir}}/missing.md", as: path}
          steps:
            - {id: noop, mode: code, command: "true"}
        ---
        """)
    )
    (tmp_path / "items.md").write_text("---\nprogress:\n  items:\n    - item: alfa\n---\n")
    for_each = (
        """\
      for_each:
        over: deps.items
        bind: item
        bind_fields: [item]
        key: "{{item}}"
"""
        if mapped
        else ""
    )
    process = tmp_path / "parent.process.md"
    process.write_text(
        textwrap.dedent("""\
        ---
        process:
          name: parent
          deps:
            child: {path: ./child.process.md, as: path}
            items: {path: ./items.md, as: path}
          steps:
            - id: child
              mode: composite
              uses: deps.child
        """)
        + for_each
        + "---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=boundary",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "boundary"
    child = run / "child" / "alfa" if mapped else run / "child"
    expected_path = str(child / "missing.md")
    child_status = read_yaml_file(child / ".state" / "process-status.yaml")
    assert child_status["state"] == "failed"
    assert expected_path in child_status["error"]
    parent_status = scan_run_status(run, include_system=False)
    assert parent_status.process_error and expected_path in parent_status.process_error
    if mapped:
        failure = collect_item_outcomes(run, "child")[0]["output_failures"][0]
    else:
        failure = read_yaml_file(run / ".state" / "process-status.yaml")["steps"]["child"][
            "output_failures"
        ][0]
    assert failure["output"] == "report"
    assert failure["path"] == expected_path
    assert failure["kind"] == "missing"
    assert failure["message"] == f"path does not exist: {expected_path}"


@pytest.mark.parametrize("missing_input", [False, True])
def test_manual_refusal_finishes_step_with_the_actual_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_input: bool,
) -> None:
    process = tmp_path / "manual.process.md"
    inputs = (
        "      inputs:\n        prerequisite: {path: '{{run.dir}}/missing-input.txt'}\n"
        if missing_input
        else ""
    )
    process.write_text(
        "---\nprocess:\n  name: manual\n  steps:\n    - id: approve\n      mode: manual\n"
        + inputs
        + "---\n"
    )
    monkeypatch.setattr("metaproc.commands.run_process.MANUAL_ACK_TIMEOUT_S", 0)
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=timeout",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "timeout"
    state = read_status_at(run / ".state" / "tasks" / "approve")
    if missing_input:
        assert state is None  # Input rejection happens before an attempt starts.
        error = "missing-input.txt"
    else:
        assert state is not None and state.state == "failed"
        assert state.error and "manual acknowledgment not received" in state.error
        error = state.error
    status = scan_run_status(run, include_system=False)
    assert status.process_execution_state == "failed"
    assert status.process_error and error in status.process_error


def test_root_output_refusal_is_a_failed_process_even_when_all_steps_completed(
    tmp_path: Path,
) -> None:
    process = tmp_path / "root.process.md"
    process.write_text(
        textwrap.dedent("""\
        ---
        process:
          name: root
          outputs:
            report: {path: "{{run.dir}}/missing.md", as: path}
          steps:
            - {id: noop, mode: code, command: "true"}
        ---
        """)
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=root",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "root"
    raw = read_yaml_file(run / ".state" / "process-status.yaml")
    assert raw["steps"]["noop"]["state"] == "completed"
    assert raw["state"] == "failed"
    assert raw["output_failures"][0]["path"] == str(run / "missing.md")
    status = scan_run_status(run, include_system=False)
    assert status.process_error and "missing.md" in status.process_error


def test_process_output_read_error_retains_evidence_without_inventing_unresolved_paths(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory.md"
    directory.mkdir()
    spec = ProcessSpec.model_validate(
        {
            "name": "output-evidence",
            "outputs": {
                "unreadable": {"path": str(directory), "as": "path", "format": "frontmatter-md"},
                "unresolved": {"ref": "unknown.output", "as": "path"},
            },
        }
    )
    result = validate_process_outputs_detailed(spec, {}, tmp_path)
    assert len(result.errors) == 2
    assert len(result.output_failures) == 1
    failure = result.output_failures[0]
    assert failure.path == str(directory)
    assert failure.kind == "unreadable"
    assert "could not read output" in failure.message
    assert "plan is required" in result.errors[1]
