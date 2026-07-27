"""Tests for metaproc probe-tool-use command."""

from __future__ import annotations

import re
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.probe_tool_use import (
    _build_probe_config,
    _run_probe,
    _sentinel_present,
)

runner = CliRunner()


class TestBuildProbeConfig:
    """Per-harness probe config must set the keys each adapter needs to
    actually invoke tools (e.g. gemini's approval-mode yolo, pi-cli's
    provider inference)."""

    def test_gemini_enables_yolo_and_stream_json(self):
        cfg = _build_probe_config("gemini-cli", "gemini-3.5-flash", None)
        assert cfg["model"] == "gemini-3.5-flash"
        assert cfg["permission_mode"] == "bypassPermissions"
        assert cfg["output_format"] == "stream-json"

    def test_pi_infers_provider_from_model(self):
        cfg = _build_probe_config("pi-cli", "gemini-3.5-flash", None)
        assert cfg["model"] == "gemini-3.5-flash"
        # google-vertex is the inferred provider for gemini-* models.
        assert cfg["provider"] == "google-vertex"
        assert cfg["no_session_persistence"] is True

    def test_pi_honours_explicit_provider_override(self):
        cfg = _build_probe_config("pi-cli", "gemini-3.5-flash", "vertex-maas")
        assert cfg["provider"] == "vertex-maas"

    def test_claude_enables_stream_json_and_bypass(self):
        cfg = _build_probe_config("claude-code-cli", "sonnet", None)
        assert cfg["permission_mode"] == "bypassPermissions"
        assert cfg["output_format"] == "stream-json"
        assert cfg["verbose"] is True

    def test_codex_enables_bypass_only(self):
        cfg = _build_probe_config("codex-cli", "gpt-5.5", None)
        assert cfg["permission_mode"] == "bypassPermissions"


class TestRunProbeSentinelDetection:
    """The sentinel string is the only signal that a tool round-trip
    completed — model cannot hallucinate 16 random hex chars."""

    def _adapter(self) -> MagicMock:
        adapter = MagicMock()
        adapter.build_command.return_value = ["echo", "OK"]
        adapter.prepare_env.return_value = {}
        return adapter

    def test_unknown_harness_returns_failure(self):
        ok, summary, stdout = _run_probe("bogus-cli", "model-x", None, timeout_s=5)
        assert not ok
        assert "unknown harness" in summary
        assert stdout == ""

    def test_pass_when_sentinel_appears_in_stdout(self):
        with (
            patch("metaproc.commands.probe_tool_use.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__contains__ = MagicMock(return_value=True)
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            proc = MagicMock()
            proc.returncode = 0
            # Sentinel format: PROBE-SENTINEL-<16 hex chars> generated inside _run_probe.
            # The mock can echo whatever is written to its tempfile, but we don't
            # have access to the sentinel before _run_probe runs. So patch
            # secrets.token_hex to produce a deterministic value.
            with patch(
                "metaproc.commands.probe_tool_use.secrets.token_hex",
                return_value="cafebabe12345678",
            ):
                proc.stdout = "model produced text including PROBE-SENTINEL-CAFEBABE12345678 here"
                proc.stderr = ""
                mock_run.return_value = proc

                ok, summary, _ = _run_probe("gemini-cli", "gemini-3.5-flash", None, timeout_s=5)

        assert ok, summary
        assert "echoed via tool round-trip" in summary

    def test_fail_when_sentinel_missing(self):
        with (
            patch("metaproc.commands.probe_tool_use.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__contains__ = MagicMock(return_value=True)
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "model hallucinated a response without reading the file"
            proc.stderr = ""
            mock_run.return_value = proc

            ok, summary, _ = _run_probe("gemini-cli", "gemini-3.5-flash", None, timeout_s=5)

        assert not ok
        assert "sentinel" in summary
        assert "not found" in summary

    def test_fail_when_subprocess_non_zero_exit(self):
        with (
            patch("metaproc.commands.probe_tool_use.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__contains__ = MagicMock(return_value=True)
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            proc = MagicMock()
            proc.returncode = 1
            proc.stdout = ""
            proc.stderr = "auth error: missing GOOGLE_APPLICATION_CREDENTIALS"
            mock_run.return_value = proc

            ok, summary, _ = _run_probe("gemini-cli", "gemini-3.5-flash", None, timeout_s=5)

        assert not ok
        assert "exit code 1" in summary

    def test_fail_when_subprocess_times_out(self):

        with (
            patch("metaproc.commands.probe_tool_use.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__contains__ = MagicMock(return_value=True)
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=5)

            ok, summary, _ = _run_probe("gemini-cli", "gemini-3.5-flash", None, timeout_s=5)

        assert not ok
        assert "timed out" in summary


class TestSentinelPresent:
    """Sentinel detection must survive stream-json chunking (gemini-cli often
    splits the response into multiple `content` delta events) and must NOT
    false-positive on metadata fields (model IDs, timestamps)."""

    def test_plain_substring_fast_path(self):
        stdout = "preamble PROBE-SENTINEL-DEADBEEF1234ABCD trailing"
        assert _sentinel_present(stdout, "PROBE-SENTINEL-DEADBEEF1234ABCD")

    def test_concatenated_across_split_deltas(self):
        # Real gemini-cli pattern: assistant response split across consecutive
        # delta events. Sentinel only matches if we concatenate `content`
        # fields in stream order. This is the canonical failure mode the
        # fallback walker was added to handle.
        stdout = (
            '{"type":"init","model":"gemini-3.5-flash"}\n'
            '{"type":"message","role":"assistant","content":"PROBE-SENTINEL-AB","delta":true}\n'
            '{"type":"message","role":"assistant","content":"CD1234EF5678","delta":true}\n'
        )
        assert _sentinel_present(stdout, "PROBE-SENTINEL-ABCD1234EF5678")

    def test_ignores_non_text_keys(self):
        # `model`, `id`, `timestamp` etc. carry the sentinel string but they
        # are NOT text-bearing keys, so the detector must miss them. Prevents
        # a probe from false-passing on a model whose ID happens to contain
        # the sentinel substring (degenerate but worth guarding).
        stdout = (
            '{"type":"init","model":"PROBE-SENTINEL-AB","session_id":"PROBE-SENTINEL-CD"}\n'
            '{"type":"message","role":"assistant","content":"unrelated"}\n'
        )
        # Sentinel substring IS in stdout (raw), so the fast path matches.
        assert _sentinel_present(stdout, "PROBE-SENTINEL-AB")
        # But for the JSON-walk fallback path, force it to engage by using a
        # sentinel that's NOT in raw stdout but would only assemble across
        # non-text-key strings — should NOT match.
        stdout_split = (
            '{"type":"init","model":"PROBE-SENTINEL-FE"}\n{"type":"init","model":"DCBA9876"}\n'
        )
        assert not _sentinel_present(stdout_split, "PROBE-SENTINEL-FEDCBA9876")

    def test_returns_false_when_sentinel_absent(self):
        stdout = '{"type":"message","role":"assistant","content":"the file does not exist"}\n'
        assert not _sentinel_present(stdout, "PROBE-SENTINEL-MISSING")


class TestCliEntry:
    """End-to-end Typer command wiring."""

    def test_command_registers_and_help_works(self):

        result = runner.invoke(
            app, ["probe-tool-use", "--help"], env={"COLUMNS": "200", "TERM": "dumb"}
        )
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.output)
        plain = re.sub(r"\s+", " ", plain)
        assert "round-trip" in plain.lower()
        assert "--harness" in plain
        assert "--model" in plain

    def test_exits_non_zero_on_probe_failure(self):
        with patch(
            "metaproc.commands.probe_tool_use._run_probe",
            return_value=(False, "mocked failure", "fake stdout"),
        ):
            result = runner.invoke(
                app,
                [
                    "probe-tool-use",
                    "--harness",
                    "gemini-cli",
                    "--model",
                    "gemini-3.5-flash",
                ],
            )
        assert result.exit_code != 0

    def test_exits_zero_on_probe_success(self):
        with patch(
            "metaproc.commands.probe_tool_use._run_probe",
            return_value=(True, "sentinel echoed", "fake stdout"),
        ):
            result = runner.invoke(
                app,
                [
                    "probe-tool-use",
                    "--harness",
                    "gemini-cli",
                    "--model",
                    "gemini-3.5-flash",
                ],
            )
        assert result.exit_code == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
