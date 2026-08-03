"""Tests for metaproc status --remote / run-id auto-detect.

Covers Phase 2 of plan-2026-04-20-metaproc-status-logs-unification.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.status import _looks_like_run_id
from metaproc.errors import CLIError


class TestLooksLikeRunId:
    @pytest.mark.parametrize(
        "target",
        [
            "mine-2026-04-09",
            "cloud-500-ds-2026-04-12",
            "eval-v2-batch",
            "a-b-c",  # minimum segment count
            "run-20260802T193000Z.1234560000.abc123def4",
        ],
    )
    def test_valid_run_ids(self, target: str) -> None:
        assert _looks_like_run_id(target) is True

    def test_wide_typed_run_id_is_not_limited_by_legacy_gcp_label_width(self) -> None:
        target = f"run-20260802T193000Z.1234560000.{'a' * 50}"
        assert len(target) > 63
        assert _looks_like_run_id(target) is True

    @pytest.mark.parametrize(
        "target",
        [
            "./relative/path",
            "/abs/path",
            "Upper",
            "abc",  # bare word — needs hyphenated segments to be a run-id
            "eval-v2",  # only two segments — too generic to auto-detect
            "ab",
            "with space",
            "trailing-",
            "-leading",
            "x" * 64,  # exceeds GCP 63-char label limit
        ],
    )
    def test_invalid_run_ids(self, target: str) -> None:
        assert _looks_like_run_id(target) is False


class TestStatusRemote:
    """End-to-end test of `metaproc status <run-id>` → SSH exec → JSON render."""

    REMOTE_JSON_PAYLOAD = {
        "run_dir": "/mnt/filestore/runs/cloud-test-run",
        "started_at": None,
        "elapsed": None,
        "is_active": False,
        "pending_retries": 0,
        "variants": [],
        "totals": {
            "total": 0,
            "completed": 0,
            "cached": 0,
            "running": 0,
            "failed": 0,
            "pending": 0,
        },
        "system": None,
    }

    def test_auto_detects_run_id_and_execs_remote(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "test-proj")
        stdout_json = json.dumps(self.REMOTE_JSON_PAYLOAD)

        with patch(
            "metaproc.commands.status.run_remote_metaproc",
            return_value=stdout_json,
        ) as mock_run:
            result = runner.invoke(app, ["status", "cloud-test-run"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_count == 1
        remote_args = mock_run.call_args.args[0]
        assert remote_args[:2] == ["status", "/mnt/filestore/runs/cloud-test-run"]
        assert "--format" in remote_args
        assert "json" in remote_args
        assert "cloud-test-run" in result.output

    def test_remote_flag_forces_remote_even_when_local_path_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = CliRunner()
        (tmp_path / "cloud-test-run").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "test-proj")
        stdout_json = json.dumps(self.REMOTE_JSON_PAYLOAD)

        with patch(
            "metaproc.commands.status.run_remote_metaproc",
            return_value=stdout_json,
        ) as mock_run:
            result = runner.invoke(app, ["status", "cloud-test-run", "--remote"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_count == 1

    def test_local_path_takes_precedence_without_remote_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plain local path that exists should be treated as local."""
        runner = CliRunner()
        local = tmp_path / "local-run-2026-04-24"
        local.mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("metaproc.commands.status.run_remote_metaproc") as mock_run:
            runner.invoke(app, ["status", str(local)])

        # Local path present — remote helper must not be called.
        # (Exit code may be non-zero because the dir has no run content.)
        assert mock_run.call_count == 0

    def test_json_format_passes_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "test-proj")
        stdout_json = json.dumps(self.REMOTE_JSON_PAYLOAD)

        with patch(
            "metaproc.commands.status.run_remote_metaproc",
            return_value=stdout_json,
        ):
            result = runner.invoke(app, ["status", "cloud-test-run", "--format", "json"])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["run_dir"] == "/mnt/filestore/runs/cloud-test-run"

    def test_invalid_format_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["status", "cloud-test-run", "--format", "yaml"])
        assert result.exit_code != 0

    def test_cloud_runs_dir_rejected_in_remote_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--cloud-runs-dir is a local-mode flag; using it with a run-id
        (which routes remote) must be rejected, not silently dropped.
        """

        runner = CliRunner()
        monkeypatch.chdir(tmp_path)

        with patch("metaproc.commands.status.run_remote_metaproc") as mock_run:
            result = runner.invoke(
                app,
                [
                    "status",
                    "cloud-test-run",
                    "--cloud-runs-dir",
                    "/some/cloud/path",
                ],
            )

        assert result.exit_code != 0
        assert isinstance(result.exception, CLIError)
        assert "--cloud-runs-dir" in str(result.exception)
        # Remote helper must not have been called.
        assert mock_run.call_count == 0
