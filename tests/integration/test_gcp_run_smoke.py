"""Real-Batch smoke tests for ``metaproc gcp run`` (G5).

Skipped unless ``METAPROC_RUN_LIVE_GCP_TESTS=1`` is explicitly set alongside
``METAPROC_GCP_PROJECT`` and ``METAPROC_GCP_CONTAINER_IMAGE``. Each case dispatches a
Batch job and waits for a terminal state, so an ordinary developer environment cannot
incur cloud cost accidentally.

Run with::

    METAPROC_GCP_PROJECT=... METAPROC_GCP_CONTAINER_IMAGE=... \\
        uv run pytest tests/integration/test_gcp_run_smoke.py -v
"""

from __future__ import annotations

import os
from importlib.metadata import version

import pytest
import typer
from typer.testing import CliRunner

from metaproc.commands.gcp_run import run_command

REQUIRED_ENV = (
    "METAPROC_RUN_LIVE_GCP_TESTS",
    "METAPROC_GCP_PROJECT",
    "METAPROC_GCP_CONTAINER_IMAGE",
)

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("METAPROC_RUN_LIVE_GCP_TESTS") != "1"
        or any(not os.environ.get(v) for v in REQUIRED_ENV[1:]),
        reason=f"Requires real GCP env: {', '.join(REQUIRED_ENV)}",
    ),
    # Batch task spinup is ~1-3 min; full path with wheel + workspace
    # uploads can run 5-7 min. 900s gives margin without losing the
    # backstop entirely.
    pytest.mark.timeout(900),
]


def _runner_app() -> typer.Typer:
    app = typer.Typer()
    app.command("run")(run_command)
    return app


class TestGcpRunSmoke:
    def test_default_path_dispatches_echo_and_logs_capture_output(self):
        """Full default path: build wheel + workspace tarball + dispatch echo."""
        runner = CliRunner()
        result = runner.invoke(
            _runner_app(),
            ["--no-filestore", "echo", "hello", "from", "gcp-run"],
        )
        assert result.exit_code == 0, result.output
        # The echo'd line should land in the streamed Cloud Logging output.
        assert "hello from gcp-run" in result.output or "hello" in result.output

    def test_no_wheel_no_workspace_uses_baked_image(self):
        """Fast path: skip wheel + workspace, image-baked metaproc only."""
        runner = CliRunner()
        result = runner.invoke(
            _runner_app(),
            [
                "--no-wheel",
                "--no-workspace",
                "--no-filestore",
                "echo",
                "baked-image-only",
            ],
        )
        assert result.exit_code == 0, result.output

    def test_timeout_yields_nonzero_exit(self):
        """``--timeout 60`` with ``sleep 120`` should fail with a non-zero exit."""
        runner = CliRunner()
        result = runner.invoke(
            _runner_app(),
            [
                "--no-wheel",
                "--no-workspace",
                "--no-filestore",
                "--timeout",
                "60",
                "sleep",
                "120",
            ],
        )
        assert result.exit_code != 0, result.output

    def test_shipped_wheel_is_the_one_running(self):
        """Confirms the current-branch wheel replaced the image-baked metaproc.

        The local wheel version includes a dev suffix
        (``0.0.0.post<n>.dev0+<sha>``) generated at build time, which is
        extremely unlikely to match whatever was baked into the image.
        Dispatch a command that prints the installed version and assert
        the output matches the local build. If the entrypoint silently
        fell back to the image-baked metaproc (wheel install skipped or
        masked), the versions would differ.
        """

        expected_version = version("metaproc")

        runner = CliRunner()
        result = runner.invoke(
            _runner_app(),
            [
                "--no-filestore",
                "python",
                "-c",
                "from importlib.metadata import version; print(version('metaproc'))",
            ],
        )
        assert result.exit_code == 0, result.output
        assert expected_version in result.output, (
            f"Expected shipped wheel version {expected_version!r} in output, got: {result.output}"
        )
