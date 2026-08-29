"""Tests for metaproc commands — helpers and command implementations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.helpers import (
    enforce_no_unresolved_placeholders,
    find_step_def,
    load_process_spec,
    parse_adapter_config,
    parse_var_args,
    relpath,
    resolve_gcp_worker_runs_dir,
    resolve_record_output_paths,
    seed_runtime_vars,
)
from metaproc.errors import CLIError, ValidationError
from metaproc.models.authored import IOSpec, ProcessSpec, ProcessStep

runner = CliRunner()


# ── parse_var_args ───────────────────────────────────────────────


class TestParseVarArgs:
    def test_basic(self):
        result = parse_var_args(["FOO=bar", "BAZ=qux"])
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_empty(self):
        assert parse_var_args([]) == {}

    def test_value_with_equals(self):
        result = parse_var_args(["KEY=val=ue"])
        assert result == {"KEY": "val=ue"}

    def test_invalid_raises(self):
        with pytest.raises(ValidationError, match="KEY=VALUE"):
            parse_var_args(["nope"])


class TestParseAdapterConfig:
    def test_basic(self):
        result = parse_adapter_config(["model=opus", "timeout_s=300"])
        assert result == {"model": "opus", "timeout_s": "300"}

    def test_invalid_raises(self):
        with pytest.raises(ValidationError, match="KEY=VALUE"):
            parse_adapter_config(["bad"])


class TestSeedRuntimeVars:
    def test_populates_runs_dir_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("RUNS_DIR", "/mnt/filestore/runs")
        assert seed_runtime_vars({"RUN_ID": "run-1"}) == {
            "RUN_ID": "run-1",
            "RUNS_DIR": "/mnt/filestore/runs",
        }

    def test_prefers_explicit_cli_value(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("RUNS_DIR", "/mnt/filestore/runs")
        assert seed_runtime_vars({"RUNS_DIR": "/tmp/local-runs"}) == {
            "RUNS_DIR": str(Path("/tmp/local-runs").resolve())
        }

    def test_resolves_relative_runs_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        assert seed_runtime_vars({"RUNS_DIR": "runs"}) == {"RUNS_DIR": str(tmp_path / "runs")}


class TestResolveGCPWorkerRunsDir:
    def test_only_gcp_worker_uses_filestore_mount(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAPROC_GCP_FILESTORE_SERVER", "10.0.0.1")
        monkeypatch.setenv("METAPROC_GCP_FILESTORE_MOUNT_PATH", "/mnt/shared")

        assert resolve_gcp_worker_runs_dir("local") == ""
        assert resolve_gcp_worker_runs_dir("plugin-backend") == ""
        assert resolve_gcp_worker_runs_dir("gcp-worker") == "/mnt/shared/runs"


# ── find_step_def ────────────────────────────────────────────────


class TestFindStepDef:
    def test_finds_step(self):
        spec = ProcessSpec(
            name="test",
            steps=[ProcessStep(id="s1", mode="agent"), ProcessStep(id="s2", mode="agent")],
        )
        result = find_step_def(spec, "s2")
        assert result.id == "s2"

    def test_missing_step_raises(self):
        spec = ProcessSpec(name="test", steps=[ProcessStep(id="s1", mode="agent")])
        with pytest.raises(CLIError, match="not found"):
            find_step_def(spec, "missing")


# ── load_process_spec ────────────────────────────────────────────


class TestLoadProcessSpec:
    def test_valid(self, tmp_path):
        (tmp_path / "test.process.md").write_text(
            "---\nprocess:\n  name: test\n  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_paths: []\n---\n"
        )
        spec = load_process_spec(tmp_path / "test.process.md")
        assert spec.name == "test"
        assert len(spec.steps) == 1

    def test_invalid_raises(self, tmp_path):
        (tmp_path / "test.process.md").write_text("---\nrunbook:\n  title: not a process\n---\n")
        with pytest.raises((ValidationError, ValueError)):
            load_process_spec(tmp_path / "test.process.md")


# ── enforce_no_unresolved_placeholders ───────────────────────────


class TestEnforceNoUnresolved:
    def test_clean_text(self):
        enforce_no_unresolved_placeholders("no placeholders here")

    def test_allowed_placeholder(self):
        enforce_no_unresolved_placeholders("{{VARIANT}}", allowed={"VARIANT"})

    def test_unresolved_raises(self):
        with pytest.raises(ValidationError, match="unresolved"):
            enforce_no_unresolved_placeholders("{{MISSING}}")


# ── resolve_record_output_paths ──────────────────────────────────


class TestResolveOutputPaths:
    def test_basic(self):
        outputs = {"report": IOSpec(path="runs/{{DATE}}/report.md")}
        result = resolve_record_output_paths(outputs, {"DATE": "2026-01-01"})
        assert result == {"report": "runs/2026-01-01/report.md"}

    def test_empty_outputs(self):
        result = resolve_record_output_paths({}, {})
        assert result == {}


# ── relpath ──────────────────────────────────────────────────────


class TestRelpath:
    def test_relative(self, tmp_path):
        result = relpath(tmp_path)
        assert isinstance(result, Path)


# ── Command integration tests ────────────────────────────────────


class TestRunStepCommand:
    def test_missing_process_md(self, tmp_path):
        result = runner.invoke(app, ["run-step", str(tmp_path), "--step", "s1"])
        assert result.exit_code != 0

    def test_dry_run_agent(self, tmp_path):
        process_dir = tmp_path / "proc"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            "---\nprocess:\n  name: test\n"
            "  defaults:\n    adapters:\n      claude-code-cli:\n"
            "        type: claude-code-cli\n"
            "        config:\n          permission_mode: bypassPermissions\n"
            "  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_paths: []\n"
            "      outputs:\n        main:\n          path: out.md\n---\n"
        )
        result = runner.invoke(
            app,
            [
                "run-step",
                str(process_dir / "test.process.md"),
                "--step",
                "s1",
                "--dry-run",
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
            ],
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_step_not_found(self, tmp_path):
        process_dir = tmp_path / "proc"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            "---\nprocess:\n  name: test\n  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_paths: []\n---\n"
        )
        result = runner.invoke(
            app,
            [
                "run-step",
                str(process_dir / "test.process.md"),
                "--step",
                "nonexistent",
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
            ],
        )
        assert result.exit_code != 0


class TestValidateCommand:
    def test_missing_process_md(self, tmp_path):
        result = runner.invoke(app, ["validate", str(tmp_path), "--step", "s1"])
        assert result.exit_code != 0

    def test_validate_no_outputs(self, tmp_path):
        process_dir = tmp_path / "proc"
        process_dir.mkdir()
        # Create runs/default so the run_dir exists
        run_dir = tmp_path / "proc" / "runs" / "default"
        run_dir.mkdir(parents=True)
        (process_dir / "test.process.md").write_text(
            "---\nprocess:\n  name: test\n  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_paths: []\n---\n"
        )
        result = runner.invoke(
            app,
            [
                "validate",
                str(process_dir / "test.process.md"),
                "--step",
                "s1",
                "--run-root",
                str(run_dir),
            ],
        )
        # Step has no outputs to validate — should succeed (progress goes to stderr)
        assert result.exit_code == 0


class TestCheckHeadersCommand:
    def test_missing_process_md(self, tmp_path):
        result = runner.invoke(app, ["check-headers", str(tmp_path)])
        assert result.exit_code != 0

    def test_valid_process(self, tmp_path):
        process_dir = tmp_path / "proc"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            "---\nprocess:\n  name: test\n  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_paths: []\n---\n"
        )
        result = runner.invoke(app, ["check-headers", str(process_dir / "test.process.md")])
        assert result.exit_code == 0
        assert "0 error" in result.output

    def test_missing_template_fails(self, tmp_path):
        process_dir = tmp_path / "proc"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            "---\n"
            "process:\n"
            "  name: test\n"
            "  inputs:\n"
            '    form_version: { as: string, required: false, default: "v9" }\n'
            "  steps:\n"
            "    - id: s1\n"
            "      mode: agent\n"
            "      prompt_paths: []\n"
            "      outputs:\n"
            "        research:\n"
            "          path: research.md\n"
            "          kind: file\n"
            "          format: frontmatter-md\n"
            '          template: "{{form_version}}/research.template.md"\n'
            "---\n"
        )
        result = runner.invoke(app, ["check-headers", str(process_dir / "test.process.md")])
        assert result.exit_code != 0
        assert "MISSING  template" in result.output

    def test_default_backed_dependency_path(self, tmp_path):
        child = tmp_path / "children" / "live.process.md"
        child.parent.mkdir()
        child.write_text(
            "---\nprocess:\n  name: child\n  steps: []\n---\n# child\n",
            encoding="utf-8",
        )
        top = tmp_path / "top.process.md"
        top.write_text(
            "---\n"
            "process:\n"
            "  name: top\n"
            "  inputs:\n"
            "    child_name:\n"
            "      param: CHILD_NAME\n"
            "      as: string\n"
            "      required: false\n"
            "      default: live.process.md\n"
            "  deps:\n"
            '    child: { path: "./children/{{child_name}}", as: path }\n'
            "  steps:\n"
            "    - id: child\n"
            "      mode: composite\n"
            "      uses: deps.child\n"
            "---\n"
            "# top\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["check-headers", str(top)])

        assert result.exit_code == 0, result.output
        assert "0 error(s)" in result.output


class TestCompareCommand:
    def test_missing_dir(self, tmp_path):
        result = runner.invoke(app, ["compare", str(tmp_path / "a"), str(tmp_path / "b")])
        assert result.exit_code != 0

    def test_compare_two_dirs(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        for d in (dir_a, dir_b):
            d.mkdir()
            state = d / ".state"
            state.mkdir()
            (state / "status.yaml").write_text("state: completed\n")
            (state / "attempt.yaml").write_text(
                "runtime:\n  adapter_type: claude-code-cli\n  model: opus\n"
            )
            (state / "result.yaml").write_text("validated: true\n")

        result = runner.invoke(app, ["compare", str(dir_a), str(dir_b)])
        assert result.exit_code == 0
        assert "Field" in result.output
        assert "adapter" in result.output


class TestTailCommand:
    def test_once_mode(self, tmp_path):
        logs_dir = tmp_path / ".logs"
        logs_dir.mkdir()
        log_file = logs_dir / "test_ctx_2025.jsonl"
        log_file.write_text(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "abc123",
                    "model": "opus",
                    "tools": [],
                }
            )
            + "\n"
        )
        result = runner.invoke(app, ["tail", str(logs_dir), "--once"])
        assert result.exit_code == 0
        assert "init" in result.output

    def test_once_with_summary(self, tmp_path):
        logs_dir = tmp_path / ".logs"
        logs_dir.mkdir()
        log_file = logs_dir / "s1_AAPL_2025.jsonl"
        log_file.write_text(
            json.dumps({"type": "result", "is_error": False, "cost_usd": 0.05, "duration_s": 10})
            + "\n"
        )
        result = runner.invoke(app, ["tail", str(logs_dir), "--once", "--summary"])
        assert result.exit_code == 0
        assert "Summary" in result.output


class TestCompareMatrixCommand:
    def test_uses_plugin_defaults(self, tmp_path, monkeypatch):
        # compare-matrix needs plugin compare defaults to auto-detect --output
        _original = __import__(
            "metaproc.plugins.discovery", fromlist=["discover_and_load_plugins"]
        ).discover_and_load_plugins

        def _patched_discover(registry=None):
            reg = _original(registry)
            reg.register_compare_defaults(
                fields=[
                    "first_window.direction",
                    "first_window.move_pct",
                    "scored_close.direction",
                    "scored_close.move_pct",
                    "position_type",
                    "recommended_allocation_pct",
                ],
                envelope_key="prediction",
            )
            return reg

        monkeypatch.setattr(
            "metaproc.plugins.discovery.discover_and_load_plugins", _patched_discover
        )

        process_dir = tmp_path / "runs" / "predict"
        (process_dir / "claude-default" / "AAPL").mkdir(parents=True)
        (process_dir / "claude-default" / "AAPL" / "prediction.md").write_text(
            "---\nprediction:\n"
            "  first_window:\n"
            "    direction: up\n"
            "    move_pct: 1.0\n"
            "  scored_close:\n"
            "    direction: down\n"
            "    move_pct: -2.0\n"
            "  position_type: long\n"
            "  recommended_allocation_pct: 5\n"
            "---\n"
        )
        result = runner.invoke(app, ["compare-matrix", str(process_dir)])
        assert result.exit_code == 0
        assert "first_window.direction" in result.output
        assert "up" in result.output

    def test_nonexistent_dir_reports_not_found(self, tmp_path):
        missing = tmp_path / "nonexistent"
        result = runner.invoke(app, ["compare-matrix", str(missing)])
        assert result.exit_code != 0
        assert "not found" in str(result.exception or result.output)

    def test_empty_dir_reports_no_items(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = runner.invoke(
            app,
            [
                "compare-matrix",
                str(empty_dir),
                "--output",
                "prediction.md",
                "--fields",
                "direction",
            ],
        )
        assert result.exit_code != 0
        assert result.exception is not None
        assert "No items found" in str(result.exception)
