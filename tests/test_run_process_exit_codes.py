"""Process exit codes for ``metaproc run-process``.

These run the real CLI as a subprocess rather than through ``CliRunner``.
``CliRunner`` invokes the Typer app directly, so it never exercises
``metaproc.cli.main``'s ``CLIError`` → ``SystemExit(exit_code)`` translation and
reports 1 for every raised ``CLIError`` regardless of its exit code. Retry
automation reads the real process status, so that is what these assert:

  0 — the process completed
  1 — the process ran and a step failed
  2 — launch validation refused the invocation; nothing ran
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

_HANDLERS = '''\
"""Handlers for the exit-code fixtures."""

from __future__ import annotations

from pathlib import Path

from metaproc.models.authored import ProcessStep


def ok(variables: dict[str, str], step: ProcessStep) -> None:
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"{step.id}.txt").write_text("ok\\n")


def boom(variables: dict[str, str], step: ProcessStep) -> None:  # noqa: ARG001
    msg = "deliberate step failure"
    raise RuntimeError(msg)
'''


def _write_fixture(tmp_path: Path, name: str, spec_body: str) -> Path:
    (tmp_path / "handlers.py").write_text(_HANDLERS, encoding="utf-8")
    process_path = tmp_path / f"{name}.process.md"
    process_path.write_text(textwrap.dedent(spec_body), encoding="utf-8")
    return process_path


def _run(
    process_path: Path, tmp_path: Path, run_id: str, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "metaproc",
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            f"RUN_ID={run_id}",
            *extra,
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


_PASSING_SPEC = """\
    ---
    process:
      name: exit-codes-pass
      steps:
        - id: s1
          mode: code
          handler: "handlers.py:ok"
          outputs:
            out:
              path: "{{run.dir}}/s1.txt"
              kind: file
    ---
    # Passing process
    """

_FAILING_SPEC = """\
    ---
    process:
      name: exit-codes-fail
      steps:
        - id: s1
          mode: code
          handler: "handlers.py:boom"
          outputs:
            out:
              path: "{{run.dir}}/s1.txt"
              kind: file
        - id: s2
          mode: code
          handler: "handlers.py:ok"
          needs: [s1]
          outputs:
            out:
              path: "{{run.dir}}/s2.txt"
              kind: file
        - id: independent
          mode: code
          handler: "handlers.py:ok"
          outputs:
            out:
              path: "{{run.dir}}/independent.txt"
              kind: file
    ---
    # Failing process
    """


def test_successful_run_exits_zero(tmp_path: Path) -> None:
    process_path = _write_fixture(tmp_path, "pass", _PASSING_SPEC)

    result = _run(process_path, tmp_path, "ok-run")

    assert result.returncode == 0, result.stderr


def test_unresolved_placeholder_exits_two_before_any_step_runs(tmp_path: Path) -> None:
    """A spec that fails validation must not look like a completed run."""
    process_path = _write_fixture(
        tmp_path,
        "unresolved",
        """\
        ---
        process:
          name: exit-codes-unresolved
          steps:
            - id: s1
              mode: code
              handler: "handlers.py:ok"
              outputs:
                out:
                  path: "{{MISSING_ROOT}}/s1.txt"
                  kind: file
        ---
        # Unresolved placeholder
        """,
    )

    result = _run(process_path, tmp_path, "unresolved-run")

    assert result.returncode == 2, result.stderr
    assert "unresolved placeholder {{MISSING_ROOT}}" in result.stderr
    # Validation refused the launch, so no run directory was ever populated.
    assert not (tmp_path / "runs" / "unresolved-run" / ".state").exists()


def test_missing_operator_parameter_exits_two(tmp_path: Path) -> None:
    process_path = _write_fixture(
        tmp_path,
        "missing-param",
        """\
        ---
        process:
          name: exit-codes-missing-param
          inputs:
            ticker: { param: TICKER, as: string }
          steps:
            - id: s1
              mode: code
              handler: "handlers.py:ok"
              outputs:
                out:
                  path: "{{run.dir}}/s1.txt"
                  kind: file
        ---
        # Missing operator parameter
        """,
    )

    result = _run(process_path, tmp_path, "missing-param-run")

    assert result.returncode == 2, result.stderr
    assert "TICKER" in result.stderr


def test_step_failure_with_continue_on_error_exits_one(tmp_path: Path) -> None:
    """--continue-on-error keeps independent branches going; the run still fails."""
    process_path = _write_fixture(tmp_path, "fail", _FAILING_SPEC)

    result = _run(process_path, tmp_path, "continue-run", "--continue-on-error")

    assert result.returncode == 1, result.stderr
    assert "Process completed with failures: s1" in result.stderr
    # The independent branch ran to completion despite the failure.
    assert (tmp_path / "runs" / "continue-run" / "independent.txt").exists()


def test_step_failure_without_continue_on_error_exits_one(tmp_path: Path) -> None:
    process_path = _write_fixture(tmp_path, "fail", _FAILING_SPEC)

    result = _run(process_path, tmp_path, "fail-fast-run", "--no-continue-on-error")

    assert result.returncode == 1, result.stderr
    assert "Step 's1' failed (--no-continue-on-error set)" in result.stderr
