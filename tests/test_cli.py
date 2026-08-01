"""Tests for metaproc CLI — command registration and basic invocation."""

from __future__ import annotations

import pytest
from softschema import validate_artifact
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.plugins.discovery import get_plugin_registry

runner = CliRunner()


class TestCLIBasic:
    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "metaproc" in result.output

    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        # Typer returns exit code 0 or 2 for no-args-is-help depending on version
        assert result.exit_code in (0, 2)
        assert "Usage" in result.output


class TestSubcommandRegistration:
    """Verify all 10 subcommands are registered and show help."""

    def test_plan_help(self):
        result = runner.invoke(app, ["plan", "--help"])
        assert result.exit_code == 0
        assert "process" in result.output.lower()

    def test_run_step_help(self):
        result = runner.invoke(app, ["run-step", "--help"])
        assert result.exit_code == 0

    def test_run_parallel_help(self):
        result = runner.invoke(app, ["run-parallel", "--help"])
        assert result.exit_code == 0

    def test_validate_help(self):
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == 0

    def test_softschema_help(self):
        result = runner.invoke(app, ["softschema", "--help"])
        assert result.exit_code == 0
        assert "inspect" in result.output

    def test_structure_report_help(self):
        result = runner.invoke(app, ["structure-report", "--help"])
        assert result.exit_code == 0
        assert "process" in result.output.lower()

    def test_compare_help(self):
        result = runner.invoke(app, ["compare", "--help"])
        assert result.exit_code == 0

    def test_compare_matrix_help(self):
        result = runner.invoke(app, ["compare-matrix", "--help"])
        assert result.exit_code == 0

    def test_check_headers_help(self):
        result = runner.invoke(app, ["check-headers", "--help"])
        assert result.exit_code == 0

    def test_auth_check_help(self):
        result = runner.invoke(app, ["auth-check", "--help"])
        assert result.exit_code == 0

    def test_tail_help(self):
        result = runner.invoke(app, ["tail", "--help"])
        assert result.exit_code == 0

    def test_serve_subcommand_no_longer_exists(self):
        # `metaproc serve` was replaced by the standalone `metabrowser` CLI in
        # the metabrowser rename. Confirm the metaproc app no longer registers
        # it (a hard-fail, not a warning) — its absence is the rollout signal.
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code != 0


class TestPlanCommand:
    def test_plan_with_process(self, tmp_path):
        process_dir = tmp_path / "myproc"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            "---\nprocess:\n  name: test\n  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_prefix: Hello\n"
            "      outputs:\n        main:\n          path: out.md\n---\n"
        )
        result = runner.invoke(app, ["plan", str(process_dir / "test.process.md")])
        assert result.exit_code == 0
        assert "s1" in result.output

    def test_plan_missing_process_md(self, tmp_path):
        result = runner.invoke(app, ["plan", str(tmp_path)])
        assert result.exit_code != 0

    def test_plan_with_vars(self, tmp_path):
        process_dir = tmp_path / "myproc"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            "---\nprocess:\n  name: test\n  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_prefix: Run {{TAG}}\n"
            "      outputs:\n        main:\n          path: out.md\n---\n"
        )
        result = runner.invoke(
            app, ["plan", str(process_dir / "test.process.md"), "--var", "TAG=v1"]
        )
        assert result.exit_code == 0
        assert "v1" in result.output


class TestSoftschemaCommands:
    def test_softschema_validate_process_artifact(self, tmp_path):
        process_path = tmp_path / "test.process.md"
        process_path.write_text(
            "---\nprocess:\n  name: test\n  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_prefix: Hello\n"
            "      outputs:\n        main:\n          path: out.md\n---\n"
        )

        result = runner.invoke(
            app,
            [
                "softschema",
                "validate",
                str(process_path),
                "--schema",
                "metaproc:ProcessSpec/0.1",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "ok: true" in result.output

    @pytest.mark.parametrize("temporal_value", ["2026-01-01", "2026-01-01 12:34:56"])
    def test_softschema_validate_treats_bare_yaml_temporal_as_string(
        self, tmp_path, temporal_value
    ):
        process_path = tmp_path / "timestamp.process.md"
        process_path.write_text(
            f"---\nprocess:\n  name: {temporal_value}\n  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_prefix: Hello\n"
            "      outputs:\n        main:\n          path: out.md\n---\n"
        )

        result = runner.invoke(
            app,
            [
                "softschema",
                "validate",
                str(process_path),
                "--schema",
                "metaproc:ProcessSpec/0.1",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "outcome: valid" in result.output
        assert "ok: true" in result.output

    def test_softschema_compile_accepts_documented_contract_option(self, tmp_path):
        schema_path = tmp_path / "process.schema.yaml"

        result = runner.invoke(
            app,
            [
                "softschema",
                "compile",
                "metaproc.models.authored:ProcessSpec",
                "--out",
                str(schema_path),
                "--contract",
                "example:Process/v1",
            ],
        )

        assert result.exit_code == 0, result.output
        assert schema_path.exists()
        assert "example:Process/v1" in schema_path.read_text()

    def test_structure_report_writes_frontmatter_artifact(self, tmp_path):
        process_path = tmp_path / "test.process.md"
        report_path = tmp_path / "structure-report.md"
        process_path.write_text(
            "---\nprocess:\n  name: test\n  steps:\n"
            "    - id: s1\n      mode: agent\n      prompt_prefix: Hello\n"
            "      outputs:\n        main:\n          path: out.md\n          format: frontmatter-md\n"
            "          schema: metaproc:ProcessSpec/0.1\n---\n"
        )

        result = runner.invoke(
            app,
            ["structure-report", str(process_path), "--out", str(report_path)],
        )

        assert result.exit_code == 0, result.output
        assert report_path.exists()
        text = report_path.read_text()
        assert "structure_report:" in text
        assert text.count("metaproc:StructureReport/v1") == 2
        validation = validate_artifact(
            report_path,
            contract_id="metaproc:StructureReport/v1",
            registry=get_plugin_registry().softschemas,
        )
        assert validation.ok
