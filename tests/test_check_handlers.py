"""Tests for `metaproc check-handlers` command."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_handler_module(tmp_path: Path, name: str, *, two_args: bool = True) -> Path:
    """Drop a tiny handler module next to a spec, return its path."""
    body = (
        "def my_handler(context, step):\n    return None\n"
        if two_args
        else "def my_handler(context):\n    return None\n"
    )
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


def _write_process_spec(tmp_path: Path, handler_ref: str) -> Path:
    """Minimal .process.md with a single code-mode step."""
    spec_text = f"""---
process:
  name: test
  steps:
    - id: my-step
      mode: code
      handler: "{handler_ref}"
---
# test process
"""
    path = tmp_path / "test.process.md"
    path.write_text(spec_text, encoding="utf-8")
    return path


def test_check_handlers_passes_for_valid_file_handler(runner: CliRunner, tmp_path: Path) -> None:
    _write_handler_module(tmp_path, "handler_ok")
    spec = _write_process_spec(tmp_path, "handler_ok.py:my_handler")
    result = runner.invoke(app, ["check-handlers", str(spec)])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output
    assert "1 handler(s), 0 error(s)" in result.output


def test_check_handlers_fails_when_function_missing(runner: CliRunner, tmp_path: Path) -> None:
    _write_handler_module(tmp_path, "handler_missing_fn")
    spec = _write_process_spec(tmp_path, "handler_missing_fn.py:nonexistent")
    result = runner.invoke(app, ["check-handlers", str(spec)])
    assert result.exit_code != 0
    assert "FAIL" in result.output


def test_check_handlers_fails_on_too_few_params(runner: CliRunner, tmp_path: Path) -> None:
    _write_handler_module(tmp_path, "handler_short_sig", two_args=False)
    spec = _write_process_spec(tmp_path, "handler_short_sig.py:my_handler")
    result = runner.invoke(app, ["check-handlers", str(spec)])
    assert result.exit_code != 0
    assert "must accept at least 2 parameters" in result.output


def test_check_handlers_fails_when_file_missing(runner: CliRunner, tmp_path: Path) -> None:
    spec = _write_process_spec(tmp_path, "does_not_exist.py:my_handler")
    result = runner.invoke(app, ["check-handlers", str(spec)])
    assert result.exit_code != 0
    assert "FAIL" in result.output


def _write_composite_spec(tmp_path: Path, *, uses: str, deps: str = "") -> Path:
    """Minimal .process.md with a composite step that uses another process.

    ``uses`` is the value of the ``uses:`` field; ``deps`` is the optional
    ``deps:`` block (raw YAML fragment, no leading indent).
    """
    deps_block = f"  deps:\n{deps}" if deps else ""
    spec_text = f"""---
process:
  name: top
{deps_block}
  steps:
    - id: composite-step
      mode: composite
      uses: "{uses}"
---
# top process
"""
    path = tmp_path / "top.process.md"
    path.write_text(spec_text, encoding="utf-8")
    return path


def test_check_handlers_fails_on_unknown_dep_ref(runner: CliRunner, tmp_path: Path) -> None:
    """A typo in ``uses: deps.foo`` must error, not silently skip.

    Pinned by internal review (jlevy, 2026-05-02 issue 3): the previous
    behavior printed SKIP and exited 0, which made this command weaker
    than its name implied — composite handlers could fail to resolve and
    the gate would still be green.
    """
    spec = _write_composite_spec(tmp_path, uses="deps.nonexistent")
    result = runner.invoke(app, ["check-handlers", str(spec)])
    assert result.exit_code != 0, result.output
    assert "FAIL" in result.output
    assert "unknown dep" in result.output
    assert "deps.nonexistent" in result.output


def test_check_handlers_fails_on_missing_sub_process_file(
    runner: CliRunner, tmp_path: Path
) -> None:
    """``uses: ../nope.process.md`` to a non-existent file must error.

    Same root cause as the unknown-dep case: composite handlers depend on
    the referenced spec being walkable; if the file is missing, we cannot
    verify the nested handlers, so silently exiting 0 hides the bug.
    """
    spec = _write_composite_spec(tmp_path, uses="missing-subprocess.process.md")
    result = runner.invoke(app, ["check-handlers", str(spec)])
    assert result.exit_code != 0, result.output
    assert "FAIL" in result.output
    assert "missing sub-process file" in result.output
