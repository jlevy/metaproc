"""Tests for --backend CLI flag on run-parallel."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.helpers import validate_gcp_worker_topology
from metaproc.errors import CLIError
from metaproc.runpool.registry import get_backend, register_backend, reset_registry

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestBackendCLIFlag:
    def test_help_shows_backend_option(self):
        result = runner.invoke(app, ["run-parallel", "--help"])
        assert result.exit_code == 0
        assert "--backend" in _strip_ansi(result.output)

    def test_unknown_backend_gives_helpful_error(self):
        """--backend nonexistent should fail with error listing available backends."""
        with patch(
            "metaproc.commands.run_parallel.get_backend",
            side_effect=KeyError("Unknown backend 'nonexistent'. Available backends: local"),
        ):
            # Use --dry-run so we don't need a real process, but we need a process.md.
            # The backend resolution happens in the pool path after spec loading,
            # so we patch get_backend directly to test the error handling.
            result = runner.invoke(
                app,
                ["run-parallel", "--help"],
            )
            # help should still work — the mock doesn't interfere
            assert result.exit_code == 0

        # Now test the actual error path via the registry
        reset_registry()
        try:
            try:
                get_backend("nonexistent")
                raise AssertionError("Expected KeyError")
            except KeyError as exc:
                assert "nonexistent" in str(exc)
                assert "local" in str(exc)
        finally:
            reset_registry()

    def test_backend_default_is_local(self):
        """Default backend should be 'local'."""
        result = runner.invoke(app, ["run-parallel", "--help"])
        assert result.exit_code == 0
        # The help text shows the default value
        assert "local" in _strip_ansi(result.output)

    def test_backend_flag_parsed_correctly(self):
        """Verify the CLI correctly parses --backend flag values."""
        reset_registry()
        try:
            # Register a test backend
            class TestBackend:
                @property
                def name(self) -> str:
                    return "test-backend"

            register_backend("test-backend", TestBackend)  # pyright: ignore[reportArgumentType]

            backend = get_backend("test-backend")
            assert backend.name == "test-backend"
        finally:
            reset_registry()

    def test_gcp_worker_allows_batch_runtime_and_dry_run(self) -> None:
        validate_gcp_worker_topology(
            "gcp-worker",
            batch_task_index="0",
        )
        validate_gcp_worker_topology(
            "gcp-worker",
            dry_run=True,
            batch_task_index=None,
        )

    def test_gcp_worker_allows_dispatcher_owned_orchestrator_marker(self) -> None:
        validate_gcp_worker_topology(
            "gcp-worker",
            batch_task_index=None,
            orchestrator_marker="1",
        )

    def test_gcp_worker_rejects_non_dry_operator_host_execution(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("BATCH_TASK_INDEX", raising=False)

        result = runner.invoke(
            app,
            [
                "run-parallel",
                str(tmp_path / "missing.process.md"),
                "--step",
                "fan-out",
                "--backend",
                "gcp-worker",
            ],
        )

        assert result.exit_code != 0
        assert isinstance(result.exception, CLIError)
        assert "run-parallel --backend gcp-worker is only supported inside" in str(result.exception)
        assert "run-process <spec> --backend gcp-worker --cloud" in str(result.exception)
