"""Tests for metaproc override CLI subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.io.overrides import read_overrides

runner = CliRunner()


# Minimal process spec used as the --process fixture. It declares no
# deps and no step outputs, so compute_run_dir falls through to
# `{{run.dir}}` which resolves to `RUNS_DIR/RUN_ID` — matching where the
# tests stage their fake run directories.
_TEST_SPEC = """---
process:
  name: test-override-process
  steps: []
---

# Test process for override CLI tests.
"""


def _make_run(tmp_path: Path, run_id: str = "2026-04-24-test") -> tuple[Path, Path, Path]:
    """Create a RUNS_DIR with a run subdirectory + a fixture process spec.

    Returns (runs_dir, run_dir, process_spec_path).
    """
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    spec_dir = tmp_path / "process_specs"
    spec_dir.mkdir()
    spec_path = spec_dir / "test.process.md"
    spec_path.write_text(_TEST_SPEC)
    return runs_dir, run_dir, spec_path


class TestOverrideCLI:
    def test_satisfied_writes_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir, run_dir, spec_path = _make_run(tmp_path)
        monkeypatch.setenv("RUNS_DIR", str(runs_dir))
        monkeypatch.setenv("USER", "testuser")

        result = runner.invoke(
            app,
            [
                "override",
                "2026-04-24-test",
                "predict-ticker",
                "--process",
                str(spec_path),
                "--satisfied",
                "--note",
                "14/17 usable",
            ],
        )
        assert result.exit_code == 0, result.output

        doc = read_overrides(run_dir)
        assert doc is not None
        assert len(doc.entries) == 1
        assert doc.entries[0].step == "predict-ticker"
        assert doc.entries[0].action == "satisfied"
        assert doc.entries[0].by == "testuser"
        assert doc.entries[0].note == "14/17 usable"

    def test_clear_removes_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runs_dir, run_dir, spec_path = _make_run(tmp_path)
        monkeypatch.setenv("RUNS_DIR", str(runs_dir))
        monkeypatch.setenv("USER", "testuser")

        # First, create an override
        runner.invoke(
            app,
            [
                "override",
                "2026-04-24-test",
                "predict-ticker",
                "--process",
                str(spec_path),
                "--satisfied",
                "--note",
                "to be cleared",
            ],
        )
        doc = read_overrides(run_dir)
        assert doc is not None
        assert len(doc.entries) == 1

        # Now clear it
        result = runner.invoke(
            app,
            [
                "override",
                "2026-04-24-test",
                "predict-ticker",
                "--process",
                str(spec_path),
                "--clear",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Cleared" in result.output

        doc = read_overrides(run_dir)
        assert doc is not None
        assert len(doc.entries) == 0

    def test_satisfied_required_without_clear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir, _run_dir, spec_path = _make_run(tmp_path)
        monkeypatch.setenv("RUNS_DIR", str(runs_dir))

        result = runner.invoke(
            app,
            [
                "override",
                "2026-04-24-test",
                "predict-ticker",
                "--process",
                str(spec_path),
                "--note",
                "missing --satisfied",
            ],
        )
        assert result.exit_code != 0

    def test_for_downstream_multiple(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runs_dir, run_dir, spec_path = _make_run(tmp_path)
        monkeypatch.setenv("RUNS_DIR", str(runs_dir))
        monkeypatch.setenv("USER", "testuser")

        result = runner.invoke(
            app,
            [
                "override",
                "2026-04-24-test",
                "predict-ticker",
                "--process",
                str(spec_path),
                "--satisfied",
                "--note",
                "scoped",
                "--for-downstream",
                "summarize",
                "--for-downstream",
                "qa-check",
            ],
        )
        assert result.exit_code == 0, result.output

        doc = read_overrides(run_dir)
        assert doc is not None
        assert len(doc.entries) == 1
        assert sorted(doc.entries[0].for_downstream or []) == ["qa-check", "summarize"]

    def test_missing_runs_dir_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RUNS_DIR", raising=False)
        result = runner.invoke(
            app,
            [
                "override",
                "nonexistent",
                "step-a",
                "--process",
                "any/path.process.md",
                "--satisfied",
                "--note",
                "x",
            ],
        )
        assert result.exit_code != 0

    def test_missing_run_dir_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runs_dir, _run_dir, spec_path = _make_run(tmp_path, run_id="some-other-run")
        monkeypatch.setenv("RUNS_DIR", str(runs_dir))

        result = runner.invoke(
            app,
            [
                "override",
                "nonexistent-run",
                "step-a",
                "--process",
                str(spec_path),
                "--satisfied",
                "--note",
                "x",
            ],
        )
        assert result.exit_code != 0

    def test_missing_process_arg_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: internal-reference. Without --process the engine cannot
        # know the process-level run_dir layout (varies per spec). Refuse
        # at startup rather than silently writing to the wrong path.
        runs_dir, _run_dir, _spec_path = _make_run(tmp_path)
        monkeypatch.setenv("RUNS_DIR", str(runs_dir))
        result = runner.invoke(
            app,
            [
                "override",
                "2026-04-24-test",
                "predict-ticker",
                "--satisfied",
                "--note",
                "no process arg",
            ],
        )
        assert result.exit_code != 0
        assert "process" in result.output.lower()
