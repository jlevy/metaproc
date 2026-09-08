"""Tests for metaproc auth-check command."""

from __future__ import annotations

import re
import subprocess as sp
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from metaproc.adapters.base import AuthStatus
from metaproc.adapters.codex_cli import CodexCliAdapter
from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.cli import app
from metaproc.commands.auth_check import (
    _check_adapter,
    _check_gcloud_token,
    _extract_observed_model,
    _infer_pi_provider,
    _pi_list_models,
    _pi_validate_registration,
    _resolve_variant_target,
    _run_live_check,
    _run_readiness_check,
)

runner = CliRunner()


# ── Phase 1: _check_gcloud_token ──


class TestCheckGcloudToken:
    def test_returns_ok_when_token_available(self):

        fake_mod = types.ModuleType("metaproc.cloud.gcp.gcp_credentials")
        fake_mod.__dict__["get_access_token"] = MagicMock(return_value="ya29.fake-token")
        fake_mod.__dict__["get_token_expiry"] = MagicMock(return_value="2026-04-05T12:00:00Z")

        with patch.dict(sys.modules, {"metaproc.cloud.gcp.gcp_credentials": fake_mod}):
            ok, msg = _check_gcloud_token()
        assert ok
        assert "ok" in msg
        assert "google.auth" in msg

    def test_returns_fail_when_gcp_extra_missing(self):

        with patch.dict(sys.modules, {"metaproc.cloud.gcp.gcp_credentials": None}):
            ok, msg = _check_gcloud_token()
        assert not ok
        assert "not installed" in msg

    def test_returns_fail_when_credentials_error(self):

        fake_mod = types.ModuleType("metaproc.cloud.gcp.gcp_credentials")
        fake_mod.__dict__["get_access_token"] = MagicMock(
            side_effect=RuntimeError("no credentials")
        )
        fake_mod.__dict__["get_token_expiry"] = MagicMock(return_value=None)

        with patch.dict(sys.modules, {"metaproc.cloud.gcp.gcp_credentials": fake_mod}):
            ok, msg = _check_gcloud_token()
        assert not ok
        assert "error" in msg


# ── Phase 1: _check_adapter ──


class TestCheckAdapter:
    def test_reports_binary_found(self):
        results = _check_adapter("pi-cli")
        # At minimum, binary check is reported
        assert len(results) >= 1
        msgs = [msg for _, msg in results]
        assert any("pi-cli" in m for m in msgs)

    def test_all_adapters_produce_results(self):

        for adapter_type in ADAPTER_REGISTRY:
            results = _check_adapter(adapter_type)
            assert len(results) >= 1, f"No results for {adapter_type}"

    def test_unusable_binary_includes_probe_error(self):
        status = AuthStatus(
            adapter_type="codex-cli",
            cli_found=False,
            cli_path="/usr/local/bin/codex",
            credentials_found=False,
            auth_mode="none",
            details="Codex CLI is present but unusable: spawn vendor/codex ENOENT",
            setup_hint="Install the pinned Codex CLI",
        )
        with patch.object(ADAPTER_REGISTRY["codex-cli"], "check_auth", return_value=status):
            results = _check_adapter("codex-cli")

        assert any("ENOENT" in message for _, message in results)


class TestResolveVariantTarget:
    def test_exact_adapter_name_keeps_adapter(self):
        adapter_type, model_name, provider = _resolve_variant_target("claude-code-cli")
        assert adapter_type == "claude-code-cli"
        assert model_name is None
        assert provider is None


# ── Phase 3: _run_readiness_check ──


class TestRunReadinessCheck:
    def test_nonexistent_dir(self, tmp_path: Path):
        results = _run_readiness_check(tmp_path / "nonexistent")
        assert any("does not exist" in msg for _, msg in results)
        assert not any(ok for ok, _ in results)

    def test_existing_dir_no_progress(self, tmp_path: Path):
        results = _run_readiness_check(tmp_path)
        ok_map = {msg: ok for ok, msg in results}
        assert any("exists" in msg for msg in ok_map)
        assert any("no progress.md found" in msg for msg in ok_map)

    def test_existing_dir_with_progress(self, tmp_path: Path):
        progress = tmp_path / "progress.md"
        progress.write_text(
            "---\nprogress:\n  items:\n  - event_id: FOO-2025Q1\n    status: pending\n---\n"
        )
        results = _run_readiness_check(tmp_path)
        msgs = [msg for _, msg in results]
        assert any("1 items" in m for m in msgs)

    def test_writable_check(self, tmp_path: Path):
        results = _run_readiness_check(tmp_path)
        assert any("writable" in msg for _, msg in results)


# ── CLI integration ──


class TestAuthCheckCLI:
    def test_help(self):
        result = runner.invoke(app, ["auth-check", "--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--live" in plain
        assert "--run-dir" in plain
        assert "--variant" in plain

    def test_basic_run(self):
        # Should run without error (may exit 1 if gemini has no creds)
        result = runner.invoke(app, ["auth-check"])
        assert result.exit_code in (0, 1)
        assert "Disk" in result.output

    def test_run_dir_nonexistent(self, tmp_path: Path):
        result = runner.invoke(app, ["auth-check", "--run-dir", str(tmp_path / "nope")])
        assert result.exit_code == 1
        assert "does not exist" in result.output

    def test_run_dir_with_progress(self, tmp_path: Path):
        progress = tmp_path / "progress.md"
        progress.write_text(
            "---\nprogress:\n  items:\n"
            "  - event_id: A-2025Q1\n    status: pending\n"
            "  - event_id: B-2025Q2\n    status: pending\n"
            "---\n"
        )
        result = runner.invoke(app, ["auth-check", "--run-dir", str(tmp_path)])
        # Should report 2 items
        assert "2 items" in result.output

    def test_variant_scopes_phase1_to_relevant_adapter(self):
        with (
            patch(
                "metaproc.commands.auth_check.check_disk_space",
                return_value=(True, "Disk ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_gcloud_token",
                return_value=(True, "GCP ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_adapter",
                side_effect=lambda adapter_type: [(True, f"{adapter_type} ok")],
            ) as mock_check_adapter,
        ):
            result = runner.invoke(app, ["auth-check", "--variant", "pi-glm5"])

        assert result.exit_code == 0
        assert mock_check_adapter.call_count == 1
        assert mock_check_adapter.call_args.args[0] == "pi-cli"

    def test_variant_non_claude_skips_phase2b_secret_manager_probe(self, monkeypatch):
        """Phase 2b is claude-specific.

        When the operator scopes auth-check to a non-claude adapter via
        --variant, the Phase 2b Secret Manager probe must not fire even if
        METAPROC_GCP_SECRET_CLAUDE_CREDS is set in the environment. Without
        this scoping, running the per-adapter smoke for codex/pi/gemini
        reports a red signal that belongs to a different adapter entirely.
        """
        monkeypatch.setenv(
            "METAPROC_GCP_SECRET_CLAUDE_CREDS",
            "projects/P/secrets/N/versions/1",
        )
        with (
            patch(
                "metaproc.commands.auth_check.check_disk_space",
                return_value=(True, "Disk ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_gcloud_token",
                return_value=(True, "GCP ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_adapter",
                side_effect=lambda at: [(True, f"{at} ok")],
            ),
            patch(
                "metaproc.commands.auth_check._run_claude_secret_live_check",
            ) as mock_secret_check,
        ):
            result = runner.invoke(app, ["auth-check", "--variant", "codex-cli"])

        assert result.exit_code == 0, result.output
        mock_secret_check.assert_not_called()

    def test_env_secret_probe_is_advisory_after_local_claude_live_pass(self, monkeypatch):
        """The env-default Secret Manager probe is visibility for local runs.

        If the scoped local Claude adapter and live model probe passed, a slow
        or stale $METAPROC_GCP_SECRET_CLAUDE_CREDS value should not make the
        local preflight fail. Operators can still require the remote secret by
        passing --claude-secret-ref explicitly.
        """
        monkeypatch.setenv(
            "METAPROC_GCP_SECRET_CLAUDE_CREDS",
            "projects/P/secrets/N/versions/1",
        )
        with (
            patch(
                "metaproc.commands.auth_check.check_disk_space",
                return_value=(True, "Disk ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_gcloud_token",
                return_value=(True, "GCP ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_adapter",
                side_effect=lambda at: [(True, f"{at} ok")],
            ),
            patch(
                "metaproc.commands.auth_check._run_live_check",
                return_value=[(True, "claude-code-cli: live check passed (1.0s)")],
            ),
            patch(
                "metaproc.commands.auth_check._run_claude_secret_live_check",
                return_value=[(False, "claude-code-cli [secret=N/versions/1]: timed out")],
            ),
        ):
            result = runner.invoke(app, ["auth-check", "--live", "--variant", "claude-code-cli"])

        assert result.exit_code == 0, result.output
        assert "advisory" in result.output
        assert "PASS" in result.output

    def test_explicit_claude_secret_ref_remains_blocking_after_local_live_pass(self):
        with (
            patch(
                "metaproc.commands.auth_check.check_disk_space",
                return_value=(True, "Disk ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_gcloud_token",
                return_value=(True, "GCP ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_adapter",
                side_effect=lambda at: [(True, f"{at} ok")],
            ),
            patch(
                "metaproc.commands.auth_check._run_live_check",
                return_value=[(True, "claude-code-cli: live check passed (1.0s)")],
            ),
            patch(
                "metaproc.commands.auth_check._run_claude_secret_live_check",
                return_value=[(False, "claude-code-cli [secret=N/versions/1]: timed out")],
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "auth-check",
                    "--live",
                    "--variant",
                    "claude-code-cli",
                    "--claude-secret-ref",
                    "projects/P/secrets/N/versions/1",
                ],
            )

        assert result.exit_code == 1, result.output
        assert "advisory" not in result.output
        assert "FAIL" in result.output


# ── Phase 2: _run_live_check ──


class TestRunLiveCheck:
    def test_skips_when_no_binary(self):
        """Live check gracefully skips adapters without binaries."""
        with patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry:
            mock_adapter = MagicMock()
            status = MagicMock()
            status.cli_found = False
            status.credentials_found = False
            mock_adapter.check_auth.return_value = status
            mock_registry.__getitem__ = MagicMock(return_value=mock_adapter)
            results = _run_live_check("fake-adapter", None, timeout_s=5)
        assert len(results) == 1
        assert not results[0][0]
        assert "skipping" in results[0][1]

    def test_passes_on_successful_subprocess(self):
        """Live check reports pass when subprocess exits 0."""
        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_adapter = MagicMock()
            status = MagicMock()
            status.cli_found = True
            status.credentials_found = True
            mock_adapter.check_auth.return_value = status
            mock_adapter.build_command.return_value = ["echo", "OK"]
            mock_adapter.prepare_env.return_value = {}
            mock_registry.__getitem__ = MagicMock(return_value=mock_adapter)

            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            results = _run_live_check("test-adapter", None, timeout_s=5)

        assert len(results) == 1
        assert results[0][0]
        assert "passed" in results[0][1]

    def test_reports_failure_on_nonzero_exit(self):
        """Live check reports failure when subprocess exits non-zero."""
        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_adapter = MagicMock()
            status = MagicMock()
            status.cli_found = True
            status.credentials_found = True
            mock_adapter.check_auth.return_value = status
            mock_adapter.build_command.return_value = ["false"]
            mock_adapter.prepare_env.return_value = {}
            mock_registry.__getitem__ = MagicMock(return_value=mock_adapter)

            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.stderr = "auth failed"
            mock_run.return_value = mock_proc

            results = _run_live_check("test-adapter", None, timeout_s=5)

        assert len(results) == 1
        assert not results[0][0]
        assert "failed" in results[0][1]

    def test_reports_timeout(self):
        """Live check reports timeout when subprocess exceeds deadline."""

        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run", side_effect=sp.TimeoutExpired("cmd", 5)),
        ):
            mock_adapter = MagicMock()
            status = MagicMock()
            status.cli_found = True
            status.credentials_found = True
            mock_adapter.check_auth.return_value = status
            mock_adapter.build_command.return_value = ["sleep", "999"]
            mock_adapter.prepare_env.return_value = {}
            mock_registry.__getitem__ = MagicMock(return_value=mock_adapter)

            results = _run_live_check("test-adapter", None, timeout_s=5)

        assert len(results) == 1
        assert not results[0][0]
        assert "timed out" in results[0][1]


class TestRunLiveCheckAssertModel:
    """_run_live_check must verify the observed model matches --assert-model.

    A silent --model fallback (claude/gemini warn-and-default on unknown
    model names) keeps the subprocess exit code at 0, so exit-code alone
    cannot tell the caller whether the model they asked for is actually
    the one that responded. assert_model closes that loop by parsing the
    CLI's identity event and comparing the observed model to the expected.
    """

    def _adapter(self, build_cmd: list[str] | None = None) -> MagicMock:
        adapter = MagicMock()
        status = MagicMock()
        status.cli_found = True
        status.credentials_found = True
        adapter.check_auth.return_value = status
        adapter.build_command.return_value = build_cmd or ["echo", "OK"]
        adapter.prepare_env.return_value = {}
        return adapter

    def test_pass_when_observed_matches_expected(self):
        stdout_lines = (
            '{"type":"system","subtype":"init","model":"claude-sonnet-4-5","session_id":"s"}\n'
            '{"type":"result","is_error":false}\n'
        )
        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = stdout_lines
            mock_run.return_value = proc

            results = _run_live_check(
                "claude-code-cli",
                None,
                timeout_s=5,
                assert_model="sonnet-4-5",
            )

        msgs = [msg for _, msg in results]
        assert any("observed model" in m and "sonnet-4-5" in m for m in msgs)
        assert all(ok for ok, _ in results)

    def test_fail_when_observed_differs_from_expected(self):
        stdout_lines = (
            '{"type":"system","subtype":"init","model":"claude-sonnet-4-5","session_id":"s"}\n'
            '{"type":"result","is_error":false}\n'
        )
        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = stdout_lines
            mock_run.return_value = proc

            results = _run_live_check(
                "claude-code-cli",
                None,
                timeout_s=5,
                assert_model="opus-4",
            )

        assert any(not ok for ok, _ in results)
        failing = [msg for ok, msg in results if not ok]
        assert any("opus-4" in m and "sonnet-4-5" in m for m in failing)

    def test_fail_when_no_identity_event_emitted(self):
        """Missing init event = fail; operator needs to know the CLI did not
        announce its model even though the subprocess exited 0."""
        stdout_lines = '{"type":"result","is_error":false}\n'
        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = stdout_lines
            mock_run.return_value = proc

            results = _run_live_check(
                "claude-code-cli",
                None,
                timeout_s=5,
                assert_model="sonnet",
            )

        assert any(not ok for ok, _ in results)
        assert any("no model event" in msg.lower() for _, msg in results)

    def test_codex_extracts_model_from_untyped_preamble(self):
        """codex-cli's untyped preamble line carries the observed model;
        --assert-model works for codex the same way as for the other
        adapters. Required because the typed event stream
        (thread.started → turn.completed) never carries a model ID."""
        stdout_lines = (
            '{"model":"gpt-5.4","provider":"openai","workdir":"/tmp"}\n'
            '{"type":"thread.started","thread_id":"t"}\n'
            '{"type":"turn.completed","usage":{}}\n'
        )
        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = stdout_lines
            mock_run.return_value = proc

            results = _run_live_check(
                "codex-cli",
                "gpt-5-codex",
                timeout_s=5,
                assert_model="gpt-5",
            )

        assert all(ok for ok, _ in results), results
        assert any(
            "observed model" in msg and "gpt-5.4" in msg and "gpt-5" in msg for _, msg in results
        )

    def test_assert_model_substring_match(self):
        """Provider-prefixed IDs like 'zai-org/glm-5-maas' must accept a
        substring match like 'glm-5-maas' so the operator does not have to
        predict the provider prefix."""
        stdout_lines = (
            '{"type":"agent_start"}\n'
            '{"type":"message_start","message":{"role":"assistant","model":"zai-org/glm-5-maas"}}\n'
        )
        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch(
                "metaproc.commands.auth_check._pi_list_models",
                return_value=(
                    True,
                    "providers: vertex-maas\nmodels: glm-5-maas zai-org/glm-5-maas",
                ),
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = stdout_lines
            mock_run.return_value = proc

            results = _run_live_check(
                "pi-cli",
                "glm-5-maas",
                timeout_s=5,
                assert_model="glm-5-maas",
            )

        assert all(ok for ok, _ in results), results

    def test_cli_flag_wires_through(self):
        """End-to-end: the typer --assert-model flag must reach _run_live_check."""
        with (
            patch(
                "metaproc.commands.auth_check.check_disk_space",
                return_value=(True, "Disk ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_gcloud_token",
                return_value=(True, "GCP ok"),
            ),
            patch(
                "metaproc.commands.auth_check._check_adapter",
                side_effect=lambda at: [(True, f"{at} ok")],
            ),
            patch("metaproc.commands.auth_check._run_live_check") as mock_live,
        ):
            mock_live.return_value = [(True, "live ok")]
            result = runner.invoke(
                app,
                [
                    "auth-check",
                    "--live",
                    "--variant",
                    "claude-code-cli",
                    "--assert-model",
                    "sonnet",
                ],
            )

        assert result.exit_code == 0, result.output
        assert mock_live.call_count == 1
        assert mock_live.call_args.kwargs.get("assert_model") == "sonnet"


class TestRunLiveCheckCodex:
    """_run_live_check must hand the codex adapter a permission config.

    Regression guard: without an explicit permission_mode or sandbox+approval
    pair, CodexCliAdapter.build_command raises ValueError because codex
    would otherwise prompt for approval on every tool call and deadlock in
    batch mode (stdin=DEVNULL). The harness must supply a safe default so
    the smoke path does not require every caller to know codex's quirks.
    """

    def test_codex_live_check_builds_valid_command(self):

        real_codex = CodexCliAdapter()
        mock_status = MagicMock()
        mock_status.cli_found = True
        mock_status.credentials_found = True

        with (
            patch.dict(
                "metaproc.commands.auth_check.ADAPTER_REGISTRY",
                {"codex-cli": real_codex},
                clear=False,
            ),
            patch.object(real_codex, "check_auth", return_value=mock_status),
            patch("subprocess.run") as mock_run,
        ):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            results = _run_live_check("codex-cli", "gpt-5-codex", timeout_s=5)

        assert len(results) == 1
        assert results[0][0], f"expected pass, got: {results[0][1]}"

        cmd = mock_run.call_args.args[0]
        assert any(
            flag in cmd
            for flag in (
                "--dangerously-bypass-approvals-and-sandbox",
                "--sandbox",
            )
        ), f"codex command must include a permission flag; got: {cmd}"


class TestExtractObservedModel:
    """The live probe parses the CLI's JSONL output for the identity event
    that names the model the subprocess actually dispatched against. Without
    this, a silent --model fallback (claude/gemini warn-and-default) would
    false-green a live check.
    """

    def test_claude_system_init_event_yields_model(self):
        stdout = (
            '{"type":"system","subtype":"init","session_id":"abc","model":"claude-sonnet-4-5","tools":[]}\n'
            '{"type":"result","is_error":false}\n'
        )
        assert _extract_observed_model("claude-code-cli", stdout) == "claude-sonnet-4-5"

    def test_claude_returns_none_when_no_init_event(self):
        stdout = '{"type":"result","is_error":false}\n'
        assert _extract_observed_model("claude-code-cli", stdout) is None

    def test_gemini_init_event_yields_model(self):
        stdout = (
            '{"type":"init","session_id":"sess1","model":"gemini-3-pro"}\n'
            '{"type":"result","status":"success"}\n'
        )
        assert _extract_observed_model("gemini-cli", stdout) == "gemini-3-pro"

    def test_pi_message_start_event_yields_model(self):
        """pi-cli's `agent_start` is empty; the model ID shows up under
        `message.model` on the first assistant `message_start` event."""
        stdout = (
            '{"type":"agent_start"}\n'
            '{"type":"turn_start"}\n'
            '{"type":"message_start","message":{"role":"assistant","model":"zai-org/glm-5-maas"}}\n'
            '{"type":"agent_end"}\n'
        )
        assert _extract_observed_model("pi-cli", stdout) == "zai-org/glm-5-maas"

    def test_codex_extracts_from_untyped_preamble(self):
        """codex-cli 0.124.0 prints an untyped config-dump line as its
        first stdout line; the `model` field lives there (typed event
        stream does not carry model ID)."""
        stdout = (
            '{"model":"gpt-5.4","provider":"openai","workdir":"/tmp"}\n'
            '{"type":"thread.started","thread_id":"t"}\n'
            '{"type":"turn.completed","usage":{"input_tokens":10}}\n'
        )
        assert _extract_observed_model("codex-cli", stdout) == "gpt-5.4"

    def test_garbage_jsonl_is_ignored(self):
        stdout = "not json\n{broken\n"
        assert _extract_observed_model("claude-code-cli", stdout) is None

    def test_empty_stdout_returns_none(self):
        assert _extract_observed_model("claude-code-cli", "") is None

    def test_unknown_adapter_returns_none(self):
        stdout = '{"type":"init","model":"x"}\n'
        assert _extract_observed_model("unknown-adapter", stdout) is None


class TestInferPiProvider:
    def test_maas_names_infer_vertex_maas(self):
        assert _infer_pi_provider("glm-5-maas") == "vertex-maas"
        assert _infer_pi_provider("qwen3-235b-a22b-instruct-2507-maas") == "vertex-maas"
        assert _infer_pi_provider("deepseek-v3.2-maas") == "vertex-maas"

    def test_gemini_infers_google_vertex(self):
        assert _infer_pi_provider("gemini-3-flash") == "google-vertex"
        assert _infer_pi_provider("google/gemini-3-1-pro-customtools") == "google-vertex"

    def test_anthropic_aliases(self):
        assert _infer_pi_provider("opus") == "anthropic"
        assert _infer_pi_provider("sonnet") == "anthropic"
        assert _infer_pi_provider("claude-sonnet-4-6") == "anthropic"

    def test_openai_aliases(self):
        assert _infer_pi_provider("gpt-4.1") == "openai"
        assert _infer_pi_provider("o3") == "openai"
        assert _infer_pi_provider("o4-mini") == "openai"

    def test_unknown_returns_none(self):
        assert _infer_pi_provider("unknown-model") is None
        assert _infer_pi_provider("") is None
        assert _infer_pi_provider(None) is None


class TestPiValidateRegistration:
    """Behavior of the pi --list-models pre-flight gate."""

    def _patch_list_output(self, output: str):
        return patch(
            "metaproc.commands.auth_check._pi_list_models",
            return_value=(True, output),
        )

    def test_registered_provider_and_model_pass(self):
        with self._patch_list_output(
            "providers: vertex-maas\nmodels: glm-5-maas zai-org/glm-5-maas"
        ):
            ok, diag = _pi_validate_registration("vertex-maas", "glm-5-maas", env={})
        assert ok
        assert "validated" in diag

    def test_missing_provider_fails(self):
        with self._patch_list_output("providers: anthropic openai\nmodels: opus sonnet"):
            ok, diag = _pi_validate_registration("vertex-maas", "glm-5-maas", env={})
        assert not ok
        assert "vertex-maas" in diag

    def test_missing_model_fails(self):
        with self._patch_list_output("providers: vertex-maas\nmodels: deepseek-v3.2-maas"):
            ok, diag = _pi_validate_registration("vertex-maas", "glm-5-maas", env={})
        assert not ok
        assert "glm-5-maas" in diag

    def test_list_subprocess_failure_is_surfaced(self):
        with patch(
            "metaproc.commands.auth_check._pi_list_models",
            return_value=(False, "pi --list-models timed out after 15s"),
        ):
            ok, diag = _pi_validate_registration("vertex-maas", "glm-5-maas", env={})
        assert not ok
        assert "timed out" in diag

    def test_pi_list_models_uses_resolved_binary(self):
        """_pi_list_models invokes pi --list-models under the provided env."""
        with (
            patch(
                "metaproc.commands.auth_check.resolve_pi_binary",
                return_value="/usr/local/bin/pi",
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_proc = MagicMock()
            mock_proc.stdout = "vertex-maas glm-5-maas"
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc
            ok, output = _pi_list_models({"FOO": "bar"})

        assert ok
        assert "glm-5-maas" in output
        assert mock_run.call_args.args[0][0] == "/usr/local/bin/pi"
        assert mock_run.call_args.args[0][1] == "--list-models"
        assert mock_run.call_args.kwargs["env"] == {"FOO": "bar"}


class TestRunLiveCheckPiRegistrationGate:
    """_run_live_check wires the registration gate before dispatching prompts."""

    @pytest.fixture(autouse=True)
    def _stub_gcp_token(self) -> Iterator[None]:
        """Keep registration tests independent of ambient ADC and gcloud state."""
        with patch(
            "metaproc.cloud.gcp.resolve_token.resolve_gcp_token",
            return_value="test-gcp-token",
        ):
            yield

    def _adapter(self):
        adapter = MagicMock()
        status = MagicMock()
        status.cli_found = True
        status.credentials_found = True
        adapter.check_auth.return_value = status
        adapter.build_command.return_value = ["pi", "--provider", "vertex-maas", "-p", "x"]
        adapter.prepare_env.return_value = {}
        return adapter

    def test_live_check_fails_when_provider_not_registered(self):
        """A variant like pi-cli-glm-5-maas must FAIL if vertex-maas is absent."""
        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch(
                "metaproc.commands.auth_check._pi_list_models",
                return_value=(True, "providers: anthropic\nmodels: opus sonnet"),
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            results = _run_live_check("pi-cli", "glm-5-maas", timeout_s=30)

        assert len(results) == 1
        ok, msg = results[0]
        assert not ok
        assert "vertex-maas" in msg or "glm-5-maas" in msg
        assert mock_run.call_count == 0, "trivial prompt must not run when registration fails"

    def test_live_check_runs_prompt_when_registration_passes(self):
        """Happy path: registration gate passes, trivial prompt runs."""
        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch(
                "metaproc.commands.auth_check._pi_list_models",
                return_value=(True, "providers: vertex-maas\nmodels: glm-5-maas"),
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc
            mock_registry.__getitem__ = MagicMock(return_value=self._adapter())
            results = _run_live_check("pi-cli", "glm-5-maas", timeout_s=30)

        assert len(results) == 1
        assert results[0][0]
        assert mock_run.call_count == 1

    def test_explicit_provider_override_is_respected(self):
        """Operator-supplied --provider overrides naming-based inference."""
        captured: dict[str, dict[str, object]] = {}

        def capture_build_command(prompt_file, merged_config, variables):  # noqa: ARG001
            captured["merged_config"] = dict(merged_config)
            return ["pi", "-p", "x"]

        adapter = self._adapter()
        adapter.build_command.side_effect = capture_build_command

        with (
            patch("metaproc.commands.auth_check.ADAPTER_REGISTRY") as mock_registry,
            patch(
                "metaproc.commands.auth_check._pi_list_models",
                return_value=(True, "providers: vertex-maas\nmodels: glm-5-maas"),
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc
            mock_registry.__getitem__ = MagicMock(return_value=adapter)
            _run_live_check("pi-cli", "glm-5-maas", timeout_s=30, provider="vertex-maas")

        assert captured["merged_config"]["provider"] == "vertex-maas"
        assert captured["merged_config"]["model"] == "glm-5-maas"
