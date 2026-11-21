"""Tests for metaproc.adapters.codex_cli — CodexCliAdapter."""

from __future__ import annotations

import json
import logging
import os
import pickle
import stat
import subprocess
from compression import zstd
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from metaproc.adapters.base import Adapter, AuthCapableCliAdapter, AuthStatus
from metaproc.adapters.codex_cli import (
    CODEX_CREDS_ENV_VAR,
    CODEX_LEGACY_APPROVAL_POLICIES,
    PINNED_CODEX_CLI_VERSION,
    CodexCliAdapter,
    CodexCliVersionMismatch,
    _warn_legacy_approval_policy,  # pyright: ignore[reportPrivateUsage]
    codex_auth_json_mode,
    codex_rollout_model,
    codex_thread_id,
    verify_codex_version,
)
from metaproc.commands.auth_check import _evaluate_model_assertion, _run_live_check
from metaproc.config.env_vars import SECRET_VARS, MetaprocEnv
from metaproc.dispatch.slot_coordinator import apply_slot_env
from metaproc.engine import runtime as engine_runtime
from metaproc.engine.command_diagnostics import redact_diagnostic
from metaproc.logutil.parsing import LogFile
from metaproc.logutil.usage import UsageStats, compute_cost, load_pricing, sum_codex_usage
from metaproc.osutils.resource_context import _redact as _redact_resource_env
from metaproc.runpool.backend import write_invocation_sidecar
from metaproc.settings import CODEX_DEFAULT_MODEL, CODEX_VALID_EFFORTS, CODEX_VALID_MODELS

# The shape Codex writes after a ChatGPT login (`AuthDotJson` in
# codex-rs/login/src/auth/storage.rs, 0.147.0 and 0.166.0): `auth_mode` sits at
# the top level, and the `tokens` block carries no mode of its own.
_VALID_OAUTH_PAYLOAD = json.dumps(
    {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": "fake-id",
            "access_token": "fake-access",
            "refresh_token": "fake-refresh",
            "account_id": "fake-account",
        },
        "last_refresh": "2026-04-24T00:00:00Z",
    }
)

# The nested `tokens.auth_mode` spelling earlier Metaproc releases required.
_LEGACY_NESTED_OAUTH_PAYLOAD = json.dumps(
    {
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": "fake-id",
            "access_token": "fake-access",
            "refresh_token": "fake-refresh",
            "account_id": "fake-account",
            "auth_mode": "chatgpt",
        },
        "last_refresh": "2026-04-24T00:00:00Z",
    }
)

# What `codex login --with-api-key` writes at 0.166.0 (key value replaced).
_REAL_APIKEY_AUTH_JSON = json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-fake"})


class TestCodexCliAdapter:
    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_implements_protocol(self):
        assert isinstance(self.adapter, Adapter)

    def test_adapter_type(self):
        assert self.adapter.adapter_type == "codex-cli"

    def test_short_name(self):
        assert self.adapter.short_name == "codex-cli"

    def test_default_model(self):
        assert self.adapter.default_model == CODEX_DEFAULT_MODEL

    def test_working_directory_absent(self):
        assert self.adapter.working_directory({}) is None

    def test_working_directory_present(self):
        assert self.adapter.working_directory({"working_directory": "/tmp/x"}) == Path("/tmp/x")


class TestCodexBuildCommand:
    """permission_mode + tools + flag-placement matrix."""

    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def _cmd(self, tmp_path: Path, cfg: dict[str, object]) -> list[str]:
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Say hello.")
        return self.adapter.build_command(prompt, cfg, {})

    def test_missing_permission_mode_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="permission_mode"):
            self._cmd(tmp_path, {})

    def test_version_drift_warns_but_does_not_block_command_build(
        self, tmp_path: Path, monkeypatch
    ):
        # Version drift is surfaced as a prominent warning, not a hard error:
        # build_command must still produce a command so the run proceeds.
        monkeypatch.setattr(
            "metaproc.adapters.codex_cli._codex_version_drift",
            lambda: "simulated version drift",
        )
        cmd = self._cmd(tmp_path, {"permission_mode": "default"})
        assert cmd, "build_command must still build a command despite version drift"

    def test_bypass_permissions_emits_dangerous_flag(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "bypassPermissions"})
        assert cmd[:2] == ["/bin/sh", "-c"]
        assert "codex" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        # Dangerous flag must land before `exec`.
        dangerous_idx = cmd.index("--dangerously-bypass-approvals-and-sandbox")
        exec_idx = cmd.index("exec")
        assert dangerous_idx < exec_idx
        # The explicit sandbox/approval pair should NOT be emitted.
        assert "--sandbox" not in cmd
        assert "-a" not in cmd

    def test_default_permission_mode(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "default"})
        assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
        assert cmd[cmd.index("-a") + 1] == "on-request"

    def test_accept_edits_permission_mode(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "acceptEdits"})
        assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
        # Codex 0.143.0 removed `-a on-failure`; exec forces `never` anyway.
        assert cmd[cmd.index("-a") + 1] == "on-request"

    def test_plan_permission_mode(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "plan"})
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"
        # Codex 0.149.0 removed `-a untrusted`; the read-only sandbox carries plan mode.
        assert cmd[cmd.index("-a") + 1] == "on-request"

    def test_top_level_flags_precede_exec(self, tmp_path: Path):
        """-a / --search / --sandbox / -m must be top-level-only; before `exec`."""
        cmd = self._cmd(
            tmp_path,
            {"permission_mode": "plan", "tools": ["WebSearch"], "model": "gpt-6.1-sol"},
        )
        exec_idx = cmd.index("exec")
        for flag in ("--sandbox", "-a", "--search", "-m"):
            assert flag in cmd
            assert cmd.index(flag) < exec_idx, f"{flag} must precede 'exec'"

    def test_exec_only_flags_follow_exec(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "default"})
        exec_idx = cmd.index("exec")
        assert cmd[exec_idx + 1] == "--json"
        assert "--skip-git-repo-check" in cmd[exec_idx:]

    def test_luna_model_and_low_effort_are_preserved(self, tmp_path: Path) -> None:
        cmd = self._cmd(
            tmp_path, {"permission_mode": "default", "model": "gpt-5.6-luna", "effort": "low"}
        )
        assert cmd[cmd.index("-m") + 1] == "gpt-5.6-luna"
        assert "model_reasoning_effort=low" in cmd

    def test_model_validation_raises_on_unknown(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="unknown codex-cli model 'gpt-bogus'"):
            self._cmd(tmp_path, {"permission_mode": "default", "model": "gpt-bogus"})

    def test_tool_mapping_websearch_emits_search_flag(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "default", "tools": ["WebSearch"]})
        assert "--search" in cmd

    def test_tool_mapping_webfetch_emits_search_flag(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "default", "tools": ["WebFetch"]})
        assert "--search" in cmd

    def test_tool_mapping_implicit_tools_are_silent(self, tmp_path: Path):
        """Bash/Read/Write/etc. are satisfied by sandbox — no flag emitted."""
        cmd = self._cmd(
            tmp_path,
            {
                "permission_mode": "default",
                "tools": ["Bash", "Read", "Write", "Edit", "Grep", "Glob"],
            },
        )
        assert "--search" not in cmd

    def test_tool_mapping_websearch_plus_webfetch_emits_search_once(self, tmp_path: Path):
        """Both WebSearch and WebFetch trigger --search but it must appear at most once."""
        cmd = self._cmd(
            tmp_path,
            {"permission_mode": "default", "tools": ["WebSearch", "WebFetch"]},
        )
        assert cmd.count("--search") == 1

    def test_full_auto_is_never_emitted(self, tmp_path: Path):
        """Spec promise: adapter always uses explicit --sandbox + -a, never --full-auto."""
        for mode in ("bypassPermissions", "default", "acceptEdits", "plan"):
            cmd = self._cmd(tmp_path, {"permission_mode": mode})
            assert "--full-auto" not in cmd, f"--full-auto emitted for permission_mode={mode}"

    def test_missing_prompt_file_raises_filenotfound(self, tmp_path: Path):
        """Adapter fails fast rather than passing a literal '@/path' string to codex."""
        adapter = CodexCliAdapter()
        nonexistent = tmp_path / "does-not-exist.md"
        with pytest.raises(FileNotFoundError, match="prompt file does not exist"):
            adapter.build_command(nonexistent, {"permission_mode": "default"}, {})

    def test_tool_mapping_drops_unknown_tools(self, tmp_path: Path):
        """Unknown tools are log.debug-dropped, not surfaced."""
        cmd = self._cmd(
            tmp_path,
            {"permission_mode": "default", "tools": ["RandomCustomTool", "Read"]},
        )
        # No surprise flags from the unknown tool name.
        assert "RandomCustomTool" not in cmd
        assert "--random" not in cmd

    def test_prompt_is_redirected_to_stdin(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "default"})
        assert cmd[-1] == "-"
        assert Path(cmd[4]).read_text(encoding="utf-8") == "Say hello."

    def test_effort_default(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "default"})
        assert "model_reasoning_effort=medium" in cmd

    def test_effort_override(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "default", "effort": "high"})
        assert "model_reasoning_effort=high" in cmd

    def test_config_overrides_serialize_to_toml_pairs(self, tmp_path: Path):
        cmd = self._cmd(
            tmp_path,
            {"permission_mode": "default", "config_overrides": {"foo.bar": 42, "baz": "hi"}},
        )
        # Values go through json.dumps so strings stay quoted, ints stay bare.
        assert "foo.bar=42" in cmd
        assert 'baz="hi"' in cmd

    def test_working_directory_emits_cd_after_exec(self, tmp_path: Path):
        cmd = self._cmd(
            tmp_path,
            {"permission_mode": "default", "working_directory": "/tmp/x"},
        )
        # -C belongs in exec flags, not top-level.
        exec_idx = cmd.index("exec")
        assert cmd.index("-C") > exec_idx
        assert cmd[cmd.index("-C") + 1] == "/tmp/x"

    def test_append_system_prompt_inlined_above_prompt(self, tmp_path: Path):
        cmd = self._cmd(
            tmp_path,
            {
                "permission_mode": "default",
                "append_system_prompt": "Be terse. User={{NAME}}",
            },
        )
        prompt_text = Path(cmd[4]).read_text(encoding="utf-8")
        # Templates resolve; if variable absent, leaves placeholder.
        assert "Be terse" in prompt_text
        assert "Say hello." in prompt_text
        assert prompt_text.index("Be terse") < prompt_text.index("Say hello.")


class TestCodexValidateConfig:
    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_empty_config_is_valid(self):
        assert self.adapter.validate_config({}) == []

    def test_rejects_unknown_key(self):
        rejections = self.adapter.validate_config({"not_a_real_key": 1})
        assert [r.key for r in rejections] == ["not_a_real_key"]

    def test_rejects_invalid_model(self):
        rejections = self.adapter.validate_config({"model": "gpt-bogus"})
        assert any(r.key == "model" for r in rejections)

    def test_rejects_invalid_effort(self):
        rejections = self.adapter.validate_config({"effort": "extreme"})
        assert any(r.key == "effort" for r in rejections)

    def test_rejects_invalid_sandbox(self):
        rejections = self.adapter.validate_config({"sandbox": "ridiculous"})
        assert any(r.key == "sandbox" for r in rejections)

    def test_rejects_invalid_approval_policy(self):
        rejections = self.adapter.validate_config({"approval_policy": "maybe"})
        assert any(r.key == "approval_policy" for r in rejections)

    def test_rejects_invalid_permission_mode(self):
        """Catch typos like 'yolo' at plan time, not at runtime ValueError."""
        rejections = self.adapter.validate_config({"permission_mode": "yolo"})
        assert any(r.key == "permission_mode" for r in rejections)

    def test_accepts_all_valid_permission_modes(self):
        for mode in ("bypassPermissions", "default", "acceptEdits", "plan"):
            rejections = self.adapter.validate_config({"permission_mode": mode})
            assert not any(r.key == "permission_mode" for r in rejections), (
                f"{mode!r} unexpectedly rejected"
            )

    def test_clean_config_passes(self):
        assert (
            self.adapter.validate_config(
                {
                    "model": "gpt-6.1-sol",
                    "effort": "low",
                    "sandbox": "read-only",
                    "approval_policy": "never",
                    "permission_mode": "bypassPermissions",
                }
            )
            == []
        )


class TestCodexParseResultEvent:
    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_turn_completed_is_terminal(self):
        line = '{"type": "turn.completed", "usage": {"input_tokens": 1}}'
        assert self.adapter.parse_result_event(line) == {
            "type": "turn.completed",
            "usage": {"input_tokens": 1},
        }

    def test_turn_failed_is_terminal(self):
        line = '{"type": "turn.failed", "error": {"message": "401"}}'
        assert self.adapter.parse_result_event(line) == {
            "type": "turn.failed",
            "error": {"message": "401"},
        }

    def test_turn_started_is_not_terminal(self):
        assert self.adapter.parse_result_event('{"type": "turn.started"}') is None

    def test_item_completed_is_not_terminal(self):
        line = '{"type": "item.completed", "item": {"item_type": "agent_message"}}'
        assert self.adapter.parse_result_event(line) is None

    def test_intermediate_error_is_not_terminal(self):
        """codex-internal reconnect `error` events are NOT terminal."""
        line = '{"type": "error", "message": "Reconnecting... 1/5"}'
        assert self.adapter.parse_result_event(line) is None

    def test_non_json_returns_none(self):
        assert self.adapter.parse_result_event("not json at all") is None


class TestCodexPrepareEnv:
    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_sets_codex_home_when_home_present(self):
        env = {"HOME": "/workspace/user", "PATH": "/usr/bin"}
        out = self.adapter.prepare_env(env, {})
        assert out["CODEX_HOME"] == "/workspace/user/.codex"
        assert out["PATH"] == "/usr/bin"

    def test_does_not_clobber_existing_codex_home(self):
        env = {"HOME": "/workspace/user", "CODEX_HOME": "/custom/codex"}
        out = self.adapter.prepare_env(env, {})
        assert out["CODEX_HOME"] == "/custom/codex"

    def test_noop_when_home_absent(self):
        out = self.adapter.prepare_env({"PATH": "/usr/bin"}, {})
        assert "CODEX_HOME" not in out

    def test_creates_codex_home_dir(self, tmp_path: Path):
        """codex-cli aborts if CODEX_HOME points at a missing path."""
        env = {"HOME": str(tmp_path), "PATH": "/usr/bin"}
        out = self.adapter.prepare_env(env, {})
        codex_home = Path(out["CODEX_HOME"])
        assert codex_home.is_dir()
        assert stat.S_IMODE(codex_home.stat().st_mode) == 0o700


class TestCodexBootstrap:
    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_materializes_auth_json_with_correct_permissions(self, tmp_path, monkeypatch):
        monkeypatch.setenv(CODEX_CREDS_ENV_VAR, _VALID_OAUTH_PAYLOAD)

        self.adapter.bootstrap(tmp_path)

        codex_dir = tmp_path / ".codex"
        auth_file = codex_dir / "auth.json"
        assert codex_dir.is_dir()
        assert auth_file.is_file()
        assert auth_file.read_text() == _VALID_OAUTH_PAYLOAD
        # Env var must be unset so the payload doesn't leak to children.
        assert CODEX_CREDS_ENV_VAR not in os.environ

        dir_mode = stat.S_IMODE(codex_dir.stat().st_mode)
        file_mode = stat.S_IMODE(auth_file.stat().st_mode)
        assert dir_mode == 0o700, f"expected ~/.codex mode 0700, got {oct(dir_mode)}"
        assert file_mode == 0o600, f"expected auth.json mode 0600, got {oct(file_mode)}"

    def test_noop_when_env_var_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv(CODEX_CREDS_ENV_VAR, raising=False)

        self.adapter.bootstrap(tmp_path)

        assert not (tmp_path / ".codex").exists()

    def test_rejects_malformed_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv(CODEX_CREDS_ENV_VAR, "{not valid json")

        with pytest.raises(RuntimeError, match="not valid JSON"):
            self.adapter.bootstrap(tmp_path)

        assert not (tmp_path / ".codex").exists()

    def test_rejects_missing_tokens_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            CODEX_CREDS_ENV_VAR, json.dumps({"organizationUuid": "abc", "no_tokens": True})
        )

        with pytest.raises(RuntimeError, match="tokens"):
            self.adapter.bootstrap(tmp_path)

        assert not (tmp_path / ".codex").exists()

    def test_rejects_apikey_blob(self, tmp_path, monkeypatch):
        """API-key auth must arrive via OPENAI_API_KEY, not a creds blob."""
        monkeypatch.setenv(
            CODEX_CREDS_ENV_VAR,
            json.dumps({"tokens": {"auth_mode": "apikey", "access_token": "sk-..."}}),
        )

        with pytest.raises(RuntimeError, match="chatgpt|apikey"):
            self.adapter.bootstrap(tmp_path)

        assert not (tmp_path / ".codex").exists()

    def test_rejects_non_object_payload(self, tmp_path, monkeypatch):
        monkeypatch.setenv(CODEX_CREDS_ENV_VAR, '"just a string"')

        with pytest.raises(RuntimeError, match="JSON object"):
            self.adapter.bootstrap(tmp_path)

    def test_refuses_mixed_auth_before_materializing_a_slot(self, tmp_path: Path) -> None:
        blob = json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "oauth-fixture"},
                "personal_access_token": "fixture-value",
            }
        )

        with pytest.raises(RuntimeError, match="personal_access_token") as exc:
            self.adapter.materialize_credential(tmp_path, blob)

        assert "fixture-value" not in str(exc.value)
        assert not (tmp_path / ".codex").exists()


class TestCodexCheckAuth:
    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    @pytest.fixture(autouse=True)
    def _working_version_probe(self, monkeypatch):
        monkeypatch.setattr(
            "metaproc.adapters.codex_cli.verify_codex_version",
            lambda **_kwargs: PINNED_CODEX_CLI_VERSION,
        )

    def test_no_cli_reports_none(self, monkeypatch):
        monkeypatch.setattr("metaproc.adapters.codex_cli.shutil.which", lambda _: None)
        status = self.adapter.check_auth()
        assert status.cli_found is False
        assert status.auth_mode == "none"
        assert "not found" in status.details.lower()

    @pytest.fixture(autouse=True)
    def _no_ambient_codex_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)

    def test_openai_api_key_without_stored_login_is_api_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("metaproc.adapters.codex_cli.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        status = self.adapter.check_auth()
        assert status.auth_mode == "api-key"
        assert status.credentials_found is True
        assert "OPENAI_API_KEY" in status.details
        assert "CODEX_API_KEY" in status.details

    def test_chatgpt_oauth_session(self, monkeypatch, tmp_path):
        monkeypatch.setattr("metaproc.adapters.codex_cli.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)

        status = self.adapter.check_auth()
        assert status.auth_mode == "interactive-login"
        assert status.credentials_found is True

    def test_apikey_auth_json_classified_as_api_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr("metaproc.adapters.codex_cli.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "auth.json").write_text(
            json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "fixture-key"})
        )

        status = self.adapter.check_auth()
        assert status.auth_mode == "api-key"
        assert status.credentials_found is True

    def test_no_creds_reports_none_with_setup_hint(self, monkeypatch, tmp_path):
        monkeypatch.setattr("metaproc.adapters.codex_cli.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        status = self.adapter.check_auth()
        assert status.auth_mode == "none"
        assert status.credentials_found is False
        assert "OPENAI_API_KEY" in status.setup_hint
        assert "codex login" in status.setup_hint

    def test_check_auth_honors_codex_home_override(self, monkeypatch, tmp_path):
        """Per-review: a non-default $CODEX_HOME must be honored by check_auth.

        Without this, codex-cli (which does honor CODEX_HOME) and metaproc
        codex-auth (which now delegates to the same helper) would disagree
        with the adapter's check_auth about whether a session exists.
        """
        monkeypatch.setattr("metaproc.adapters.codex_cli.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # $HOME points at a tmp_path with NO ~/.codex; the adapter should
        # NOT find a session there.
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home-empty")
        # But $CODEX_HOME points at a separate dir that DOES contain a
        # valid OAuth session — the adapter should find it via the override.
        codex_home = tmp_path / "alt-codex-home"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        status = self.adapter.check_auth()
        assert status.auth_mode == "interactive-login"
        assert status.credentials_found is True
        assert str(codex_home) in status.details

    def test_unusable_cli_reports_not_found(self, monkeypatch):
        monkeypatch.setattr(
            "metaproc.adapters.codex_cli.shutil.which",
            lambda _: "/usr/local/bin/codex",
        )

        def fail_probe(**_kwargs):
            raise CodexCliVersionMismatch("launcher child executable is missing: ENOENT")

        monkeypatch.setattr("metaproc.adapters.codex_cli.verify_codex_version", fail_probe)

        status = self.adapter.check_auth()

        assert status.cli_found is False
        assert status.credentials_found is False
        assert "ENOENT" in status.details


def test_verify_codex_version_preserves_launcher_failure(monkeypatch):
    def failed_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["codex", "--version"],
            returncode=1,
            stdout="",
            stderr="spawn vendor/codex ENOENT",
        )

    monkeypatch.setattr("metaproc.adapters.codex_cli.subprocess.run", failed_run)

    with pytest.raises(CodexCliVersionMismatch, match="ENOENT"):
        verify_codex_version()


def test_verify_codex_version_accepts_repository_pin(monkeypatch):
    def successful_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["codex", "--version"],
            returncode=0,
            stdout=f"codex-cli {PINNED_CODEX_CLI_VERSION}\n",
            stderr="",
        )

    monkeypatch.setattr("metaproc.adapters.codex_cli.subprocess.run", successful_run)

    assert verify_codex_version() == PINNED_CODEX_CLI_VERSION


def test_verify_codex_version_rejects_different_version(monkeypatch):
    def mismatched_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["codex", "--version"],
            returncode=0,
            stdout="codex-cli 0.146.0-alpha.3.1\n",
            stderr="",
        )

    monkeypatch.setattr("metaproc.adapters.codex_cli.subprocess.run", mismatched_run)

    with pytest.raises(CodexCliVersionMismatch) as excinfo:
        verify_codex_version()
    message = str(excinfo.value)
    assert f"pinned={PINNED_CODEX_CLI_VERSION!r}" in message
    assert "actual='0.146.0-alpha.3.1'" in message


class TestCodexAuthCapableSubprotocol:
    """AuthCapableCliAdapter surface on CodexCliAdapter (plan-2026-04-21 P1.2b)."""

    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_satisfies_subprotocol(self):
        assert isinstance(self.adapter, AuthCapableCliAdapter)

    def test_slot_credential_filename_is_nested(self):
        # Codex writes under `.codex/auth.json`, unlike Claude's flat
        # `.credentials.json`. The filename is relative to slot_dir so
        # dispatch can compose the full path portably.
        assert self.adapter.slot_credential_filename == ".codex/auth.json"

    def test_no_default_cross_provider(self):
        # OQ9: operators declare Claude↔Codex fallback explicitly.
        assert self.adapter.compatible_fallback_adapters == []

    def test_credential_scope_env_points_codex_home_into_slot(self, tmp_path):
        env = self.adapter.credential_scope_env(tmp_path)
        assert env == {"CODEX_HOME": str(tmp_path / ".codex")}

    def test_debug_capture_args_empty_until_codex_grows_a_flag(self, tmp_path):
        # codex-cli has no equivalent of `claude -d api` today, so
        # debug_capture_args returns []. When codex grows a request-side
        # debug flag, populate analogously.
        assert self.adapter.debug_capture_args(tmp_path) == []

    def test_credential_scrub_env_drops_creds_bridge_not_api_key(self):
        scrub = self.adapter.credential_scrub_env()
        # The old env-bridge from claude_auth's era must be cleared so
        # it doesn't race the slot's auth.json.
        assert scrub[CODEX_CREDS_ENV_VAR] == ""
        # OPENAI_API_KEY is deliberately NOT scrubbed: API-key mode is
        # an explicit operator choice. The slot coordinator refuses
        # acquisition on that signal instead, parallel to the
        # ANTHROPIC_API_KEY rule for Claude.
        assert "OPENAI_API_KEY" not in scrub
        # The CODEX_API_KEY bridge prepare_env may add never reaches an OAuth slot.
        assert scrub["CODEX_API_KEY"] == ""

    def test_slot_launch_scrubs_ambient_access_token(self, tmp_path: Path) -> None:
        self.adapter.materialize_credential(tmp_path, _VALID_OAUTH_PAYLOAD)
        env = apply_slot_env(
            {"CODEX_HOME": str(tmp_path / "host"), "CODEX_ACCESS_TOKEN": "host-token"},
            scope_env=self.adapter.credential_scope_env(tmp_path),
            scrub_env=self.adapter.credential_scrub_env(),
        )

        child_env = self.adapter.prepare_env(env, {})
        assert child_env["CODEX_HOME"] == str(tmp_path / ".codex")
        assert "CODEX_ACCESS_TOKEN" not in child_env

    def test_materialize_credential_writes_auth_json_and_config_toml(self, tmp_path):
        slot = tmp_path / "slot"
        self.adapter.materialize_credential(slot, _VALID_OAUTH_PAYLOAD)
        codex_dir = slot / ".codex"
        auth_file = codex_dir / "auth.json"
        config_file = codex_dir / "config.toml"
        assert auth_file.read_text() == _VALID_OAUTH_PAYLOAD
        # Config pins the slot-local credential path so an ambient
        # OPENAI_API_KEY can't silently take over.
        toml = config_file.read_text()
        assert 'cli_auth_credentials_store = "file"' in toml
        assert 'forced_login_method = "chatgpt"' in toml
        # Perms: 0700 dir, 0600 files.
        assert stat.S_IMODE(codex_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(auth_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(config_file.stat().st_mode) == 0o600

    def test_materialize_credential_rejects_malformed_blob(self, tmp_path):
        with pytest.raises(RuntimeError, match="not valid JSON"):
            self.adapter.materialize_credential(tmp_path / "slot", "{not json")

    def test_materialize_credential_rejects_api_key_blob(self, tmp_path):
        # API-key blobs don't need pooling — they must arrive as
        # OPENAI_API_KEY through cloud secret hydration, not as a pool label.
        api_key_blob = json.dumps({"OPENAI_API_KEY": "sk-x", "tokens": {"auth_mode": "apikey"}})
        with pytest.raises(RuntimeError, match="auth_mode='apikey'|apikey"):
            self.adapter.materialize_credential(tmp_path / "slot", api_key_blob)

    def test_materialize_credential_rejects_non_object(self, tmp_path):
        with pytest.raises(RuntimeError, match="JSON object"):
            self.adapter.materialize_credential(tmp_path / "slot", json.dumps([1, 2]))

    def test_materialize_credential_rejects_missing_tokens(self, tmp_path):
        with pytest.raises(RuntimeError, match="tokens"):
            self.adapter.materialize_credential(tmp_path / "slot", json.dumps({"not_tokens": {}}))

    def test_capture_credential_reads_resolved_codex_home(self, tmp_path, monkeypatch):
        # capture_credential goes through resolve_codex_home, which
        # prefers $CODEX_HOME if set. Pin it at a tmp location so the
        # test doesn't touch the operator's real session.
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        blob = self.adapter.capture_credential()
        assert blob == _VALID_OAUTH_PAYLOAD

    def test_capture_credential_rejects_missing_auth_json(self, tmp_path, monkeypatch):
        # No auth.json → operator-actionable error mentioning codex login.
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        with pytest.raises(RuntimeError, match="codex login"):
            self.adapter.capture_credential()

    def test_classify_failure_unauthorized_is_expired(self):
        result = self.adapter.classify_failure(None, "401 Unauthorized", None)
        assert result.status == "expired"

    def test_classify_failure_invalid_grant_is_expired(self):
        result = self.adapter.classify_failure(None, "refresh failed: invalid_grant", None)
        assert result.status == "expired"

    def test_classify_failure_rate_limit_is_cooling(self):
        result = self.adapter.classify_failure(None, "HTTP 429 too_many_requests", None)
        assert result.status == "cooling"
        assert result.cooling_until_ts is None

    def test_classify_failure_generic_is_unknown(self):
        result = self.adapter.classify_failure(None, "network blip", None)
        assert result.status == "unknown"

    def test_flush_refreshed_credential_roundtrip(self, tmp_path):
        slot = tmp_path / "slot"
        self.adapter.materialize_credential(slot, _VALID_OAUTH_PAYLOAD)
        flushed = self.adapter.flush_refreshed_credential(slot)
        assert flushed == _VALID_OAUTH_PAYLOAD

    def test_flush_refreshed_credential_returns_none_when_absent(self, tmp_path):
        assert self.adapter.flush_refreshed_credential(tmp_path) is None

    def test_query_quota_usage_returns_none(self, tmp_path):
        # P3.2 wires a real signal once one exists on codex-cli.
        assert self.adapter.query_quota_usage(tmp_path) is None

    def test_bootstrap_is_noop_under_pool_run(self, tmp_path, monkeypatch):
        # METAPROC_AUTH_POOL_RUN=1 → bootstrap(home) short-circuits so
        # the slot coordinator is the sole writer.
        monkeypatch.setenv(CODEX_CREDS_ENV_VAR, _VALID_OAUTH_PAYLOAD)
        monkeypatch.setenv("METAPROC_AUTH_POOL_RUN", "1")
        self.adapter.bootstrap(tmp_path)
        assert not (tmp_path / ".codex").exists()
        # Env var survives — coordinator owns lifecycle.
        assert os.environ.get(CODEX_CREDS_ENV_VAR) == _VALID_OAUTH_PAYLOAD


# ── Codex 0.166.0 upgrade coverage ──────────────────────────────

# A real `codex exec --json` stream from Codex 0.166.0 on the API-key route
# (2026-11-18, gpt-6.1-sol, effort medium, one `echo probe-ok` shell call).
# Only the thread ID is from the capture; nothing here is secret.
_CODEX_0166_EXEC_JSON = """\
{"type":"thread.started","thread_id":"01a1ec82-4ce4-7883-b842-8ecab3846d7b"}
{"type":"turn.started"}
{"type":"item.started","item":{"id":"item_0","type":"command_execution","command":"/bin/bash -lc 'echo probe-ok'","aggregated_output":"","exit_code":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_0","type":"command_execution","command":"/bin/bash -lc 'echo probe-ok'","aggregated_output":"probe-ok\\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"probe-ok"}}
{"type":"turn.completed","usage":{"input_tokens":27644,"cached_input_tokens":13789,"cache_write_input_tokens":13849,"output_tokens":40,"reasoning_output_tokens":0}}
"""


def test_pin_is_codex_0_166_0() -> None:
    assert PINNED_CODEX_CLI_VERSION == "0.166.0"


def test_verify_codex_version_parses_0_166_0_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    def banner_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["codex", "--version"], returncode=0, stdout="codex-cli 0.166.0\n", stderr=""
        )

    monkeypatch.setattr("metaproc.adapters.codex_cli.subprocess.run", banner_run)

    assert verify_codex_version(expected="0.166.0") == "0.166.0"


class TestCodexApprovalPolicyMigration:
    """Codex 0.143.0 dropped `-a on-failure` and 0.149.0 dropped `-a untrusted`."""

    def setup_method(self) -> None:
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]
        _warn_legacy_approval_policy.cache_clear()

    def _cmd(self, tmp_path: Path, cfg: dict[str, object]) -> list[str]:
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Say hello.")
        return self.adapter.build_command(prompt, cfg, {})

    def test_permission_modes_emit_only_values_the_pin_parses(self, tmp_path: Path) -> None:
        for mode in ("default", "acceptEdits", "plan"):
            cmd = self._cmd(tmp_path, {"permission_mode": mode})
            assert cmd[cmd.index("-a") + 1] in {"on-request", "never"}, mode

    @pytest.mark.parametrize("legacy", sorted(CODEX_LEGACY_APPROVAL_POLICIES))
    def test_legacy_explicit_policy_is_translated_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, legacy: str
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="metaproc.adapters.codex_cli"):
            cmd = self._cmd(tmp_path, {"sandbox": "workspace-write", "approval_policy": legacy})

        assert cmd[cmd.index("-a") + 1] == "on-request"
        assert legacy not in cmd
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert repr(legacy) in warnings[0].getMessage()
        assert PINNED_CODEX_CLI_VERSION in warnings[0].getMessage()

    def test_legacy_policy_warns_once_per_process(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cfg: dict[str, object] = {"sandbox": "workspace-write", "approval_policy": "on-failure"}
        with caplog.at_level(logging.WARNING, logger="metaproc.adapters.codex_cli"):
            for _ in range(3):
                self._cmd(tmp_path, cfg)

        assert sum("on-failure" in r.getMessage() for r in caplog.records) == 1

    @pytest.mark.parametrize("current", ["on-request", "never"])
    def test_current_explicit_policy_passes_through_silently(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, current: str
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="metaproc.adapters.codex_cli"):
            cmd = self._cmd(tmp_path, {"sandbox": "read-only", "approval_policy": current})

        assert cmd[cmd.index("-a") + 1] == current
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    @pytest.mark.parametrize("legacy", sorted(CODEX_LEGACY_APPROVAL_POLICIES))
    def test_legacy_policy_still_validates(self, legacy: str) -> None:
        # Existing process files keep validating; translation happens at build time.
        assert self.adapter.validate_config({"approval_policy": legacy}) == []


class TestCodexEffortAndModels:
    def setup_method(self) -> None:
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def _cmd(self, tmp_path: Path, cfg: dict[str, object]) -> list[str]:
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Say hello.")
        return self.adapter.build_command(prompt, cfg, {})

    def test_persistent_effort_is_rejected(self) -> None:
        # Codex 0.166.0 parses `persistent`, but it selects persistent-execution
        # mode, no bundled model lists it, and the API answered HTTP 400 for
        # gpt-6.1-sol (2026-11-18), so Metaproc refuses it at plan time.
        assert "persistent" not in CODEX_VALID_EFFORTS
        rejections = self.adapter.validate_config({"effort": "persistent"})
        assert [r.key for r in rejections] == ["effort"]

    @pytest.mark.parametrize("model", sorted(CODEX_VALID_MODELS))
    def test_every_accepted_model_reaches_argv_unchanged(self, tmp_path: Path, model: str) -> None:
        cmd = self._cmd(tmp_path, {"permission_mode": "default", "model": model})
        assert cmd[cmd.index("-m") + 1] == model
        assert cmd.index("-m") < cmd.index("exec")

    def test_default_model_reaches_argv(self, tmp_path: Path) -> None:
        cmd = self._cmd(tmp_path, {"permission_mode": "default"})
        assert cmd[cmd.index("-m") + 1] == CODEX_DEFAULT_MODEL

    def test_argv_matches_live_probe_shape(self, tmp_path: Path) -> None:
        # The argv shape validated against Codex 0.166.0 on 2026-11-18.
        cmd = self._cmd(
            tmp_path,
            {"permission_mode": "default", "model": "gpt-6.1-sol", "working_directory": "/w"},
        )
        codex = cmd[cmd.index("codex") :]
        assert codex == [
            "codex",
            "-m",
            "gpt-6.1-sol",
            "-c",
            "model_reasoning_effort=medium",
            "--sandbox",
            "workspace-write",
            "-a",
            "on-request",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-C",
            "/w",
            "-",
        ]


class TestCodexAuthJsonShapes:
    """Codex writes `auth_mode` at the top level; older Metaproc read `tokens.auth_mode`."""

    def setup_method(self) -> None:
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (json.loads(_VALID_OAUTH_PAYLOAD), "chatgpt"),
            (json.loads(_LEGACY_NESTED_OAUTH_PAYLOAD), "chatgpt"),
            (json.loads(_REAL_APIKEY_AUTH_JSON), "apikey"),
            # Pre-`auth_mode` files follow Codex's `resolved_mode` fallback.
            ({"OPENAI_API_KEY": None, "tokens": {"access_token": "a"}}, "chatgpt"),
            ({"OPENAI_API_KEY": "sk-fake"}, "apikey"),
            ({"personal_access_token": "pat"}, "personalAccessToken"),
            # The top-level field wins over a stale nested one, as in Codex.
            ({"auth_mode": "apikey", "tokens": {"auth_mode": "chatgpt"}}, "apikey"),
            ({}, None),
        ],
    )
    def test_codex_auth_json_mode(self, payload: dict[str, object], expected: str | None) -> None:
        assert codex_auth_json_mode(payload) == expected

    def test_materialize_accepts_real_and_legacy_shapes(self, tmp_path: Path) -> None:
        for name, blob in (
            ("real", _VALID_OAUTH_PAYLOAD),
            ("legacy", _LEGACY_NESTED_OAUTH_PAYLOAD),
        ):
            slot = tmp_path / name
            self.adapter.materialize_credential(slot, blob)
            assert (slot / ".codex" / "auth.json").read_text() == blob

    def test_materialize_rejects_real_apikey_auth_json(self, tmp_path: Path) -> None:
        # The real API-key file has no `tokens` block at all.
        with pytest.raises(RuntimeError, match="tokens"):
            self.adapter.materialize_credential(tmp_path / "slot", _REAL_APIKEY_AUTH_JSON)

    def test_materialize_rejects_top_level_apikey_with_tokens(self, tmp_path: Path) -> None:
        blob = json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk", "tokens": {}})
        with pytest.raises(RuntimeError, match="auth_mode='apikey'"):
            self.adapter.materialize_credential(tmp_path / "slot", blob)

    def test_bootstrap_accepts_legacy_nested_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.setenv(CODEX_CREDS_ENV_VAR, _LEGACY_NESTED_OAUTH_PAYLOAD)
        self.adapter.bootstrap(tmp_path)
        assert (tmp_path / ".codex" / "auth.json").read_text() == _LEGACY_NESTED_OAUTH_PAYLOAD

    @pytest.mark.parametrize(
        ("blob", "auth_mode"),
        [
            (_VALID_OAUTH_PAYLOAD, "interactive-login"),
            (_LEGACY_NESTED_OAUTH_PAYLOAD, "interactive-login"),
            (_REAL_APIKEY_AUTH_JSON, "api-key"),
        ],
    )
    def test_check_auth_classifies_auth_json_shapes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, blob: str, auth_mode: str
    ) -> None:
        monkeypatch.setattr(
            "metaproc.adapters.codex_cli.verify_codex_version",
            lambda **_kwargs: PINNED_CODEX_CLI_VERSION,
        )
        monkeypatch.setattr("metaproc.adapters.codex_cli.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "auth.json").write_text(blob)

        status = self.adapter.check_auth()

        assert status.auth_mode == auth_mode
        assert status.credentials_found is True

    def test_check_auth_reports_unclassified_mode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "metaproc.adapters.codex_cli.verify_codex_version",
            lambda **_kwargs: PINNED_CODEX_CLI_VERSION,
        )
        monkeypatch.setattr("metaproc.adapters.codex_cli.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "bedrockApiKey"}))

        status = self.adapter.check_auth()

        assert status.auth_mode == "unknown"
        assert "bedrockApiKey" in status.details


class TestCodex0166ExecJsonStream:
    """The captured 0.166.0 stream flows through every Metaproc Codex reader."""

    def test_parse_result_event_finds_only_the_terminal_line(self) -> None:
        adapter = CodexCliAdapter()
        terminal = [
            event
            for line in _CODEX_0166_EXEC_JSON.splitlines()
            if (event := adapter.parse_result_event(line)) is not None
        ]
        assert len(terminal) == 1
        assert terminal[0]["type"] == "turn.completed"

    def test_usage_splits_cache_reads_and_writes_out_of_input(self) -> None:
        terminal = json.loads(_CODEX_0166_EXEC_JSON.splitlines()[-1])
        stats = sum_codex_usage(terminal)
        assert stats.cache_read_tokens == 13789
        assert stats.cache_write_tokens == 13849
        assert stats.input_tokens == 27644 - 13789 - 13849
        assert stats.output_tokens == 40
        assert stats.total_tokens == 27644 + 40
        assert stats.provider == "openai"

    def test_usage_without_cache_write_field_is_unchanged(self) -> None:
        # Releases before `cache_write_input_tokens` (and gpt-5.5, which reports 0).
        stats = sum_codex_usage(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 1000, "cached_input_tokens": 200, "output_tokens": 5},
            }
        )
        assert stats.input_tokens == 800
        assert stats.cache_read_tokens == 200
        assert stats.cache_write_tokens == 0

    def test_usage_never_goes_negative(self) -> None:
        stats = sum_codex_usage(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 8,
                    "cache_write_input_tokens": 8,
                    "output_tokens": 1,
                },
            }
        )
        assert stats.input_tokens == 0

    def test_log_file_reads_tool_call_and_usage(self, tmp_path: Path) -> None:
        log_file = tmp_path / "codex_run.jsonl"
        log_file.write_text(_CODEX_0166_EXEC_JSON)

        lf = LogFile(log_file, 0)
        events = lf.read_new_events()

        assert lf.done is True
        assert lf.is_error is False
        assert lf.usage_stats is not None
        assert lf.usage_stats.cache_write_tokens == 13849
        tool_events = [e for e in events if e.tool_name]
        assert tool_events, "the command_execution item must surface as a tool event"
        assert any("probe-ok" in e.summary for e in events)


# ── OPENAI_API_KEY → CODEX_API_KEY bridge ───────────────────────


def _isolate_codex_config_layers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point Codex's system layers and the working directory into *tmp_path*.

    The bridge reads every config layer Codex 0.166.0 reads, so a host
    `/etc/codex` or a `.codex/config.toml` above the checkout must not leak in.
    """
    system_dir = tmp_path / "etc-codex"
    system_dir.mkdir()
    monkeypatch.setattr("metaproc.adapters.codex_cli._CODEX_UNIX_SYSTEM_DIR", system_dir)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)


class TestCodexApiKeyBridge:
    """`codex exec` reads CODEX_API_KEY from the environment and ignores OPENAI_API_KEY."""

    def setup_method(self) -> None:
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    @pytest.fixture(autouse=True)
    def _isolated(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        _isolate_codex_config_layers(monkeypatch, tmp_path)

    def _env(self, tmp_path: Path, **extra: str) -> dict[str, str]:
        return {"HOME": str(tmp_path), "OPENAI_API_KEY": "sk-openai", **extra}

    def test_access_token_blocks_the_bridge(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Codex uses CODEX_ACCESS_TOKEN ahead of a stored login; the env key would outrank it.
        with caplog.at_level(logging.INFO, logger="metaproc.adapters.codex_cli"):
            out = self.adapter.prepare_env(self._env(tmp_path, CODEX_ACCESS_TOKEN="pat"), {})
        assert "CODEX_API_KEY" not in out
        assert any("CODEX_ACCESS_TOKEN" in r.getMessage() for r in caplog.records)

    def test_blank_access_token_does_not_block(self, tmp_path: Path) -> None:
        out = self.adapter.prepare_env(self._env(tmp_path, CODEX_ACCESS_TOKEN="  "), {})
        assert out["CODEX_API_KEY"] == "sk-openai"

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_codex_api_key_counts_as_unset(self, tmp_path: Path, blank: str) -> None:
        # Codex trims CODEX_API_KEY and ignores a blank one, so it must not block the bridge.
        out = self.adapter.prepare_env(self._env(tmp_path, CODEX_API_KEY=blank), {})
        assert out["CODEX_API_KEY"] == "sk-openai"

    def test_bridged_key_is_trimmed(self, tmp_path: Path) -> None:
        out = self.adapter.prepare_env(self._env(tmp_path, OPENAI_API_KEY=" sk-openai\n"), {})
        assert out["CODEX_API_KEY"] == "sk-openai"

    def test_bridge_is_logged_at_info(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="metaproc.adapters.codex_cli"):
            self.adapter.prepare_env(self._env(tmp_path), {})
        assert any("billed to the API key" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("name", ["config.toml", "requirements.toml", "managed_config.toml"])
    @pytest.mark.parametrize("store", ["keyring", "auto"])
    def test_keyring_store_in_a_system_layer_blocks_the_bridge(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, name: str, store: str
    ) -> None:
        (tmp_path / "etc-codex" / name).write_text(f'cli_auth_credentials_store = "{store}"\n')
        with caplog.at_level(logging.INFO, logger="metaproc.adapters.codex_cli"):
            out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert "CODEX_API_KEY" not in out
        assert any(name in r.getMessage() for r in caplog.records)

    def test_file_store_in_a_system_layer_allows_the_bridge(self, tmp_path: Path) -> None:
        (tmp_path / "etc-codex" / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert out["CODEX_API_KEY"] == "sk-openai"

    def test_unparseable_system_layer_blocks_the_bridge(self, tmp_path: Path) -> None:
        (tmp_path / "etc-codex" / "requirements.toml").write_text("not = [valid\n")
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert "CODEX_API_KEY" not in out

    def test_project_layer_in_working_directory_blocks_the_bridge(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        (project / ".codex").mkdir(parents=True)
        (project / ".codex" / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
        cfg: dict[str, object] = {"working_directory": str(project / "sub")}
        out = self.adapter.prepare_env(self._env(tmp_path), cfg)
        assert "CODEX_API_KEY" not in out

    def test_project_layer_in_process_cwd_blocks_the_bridge(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "project"
        (project / ".codex").mkdir(parents=True)
        (project / ".codex" / "config.toml").write_text('cli_auth_credentials_store = "auto"\n')
        monkeypatch.chdir(project)
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert "CODEX_API_KEY" not in out

    def test_codex_home_is_not_read_twice_as_a_project_layer(self, tmp_path: Path) -> None:
        # HOME/.codex is CODEX_HOME here and also a parent's `.codex`; Codex skips it
        # as a project layer, and a file store there must not block the bridge.
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
        out = self.adapter.prepare_env(self._env(tmp_path), {"working_directory": str(tmp_path)})
        assert out["CODEX_API_KEY"] == "sk-openai"

    def test_file_override_does_not_clear_a_keyring_layer(self, tmp_path: Path) -> None:
        # Fail safe: a lower layer that may hold a keyring login blocks the bridge
        # whatever a higher layer says.
        (tmp_path / "etc-codex" / "config.toml").write_text(
            'cli_auth_credentials_store = "keyring"\n'
        )
        cfg: dict[str, object] = {"config_overrides": {"cli_auth_credentials_store": "file"}}
        out = self.adapter.prepare_env(self._env(tmp_path), cfg)
        assert "CODEX_API_KEY" not in out

    def test_macos_managed_preferences_block_the_bridge(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        managed = tmp_path / "Managed Preferences"
        (managed / "someone").mkdir(parents=True)
        (managed / "someone" / "com.openai.codex.plist").write_bytes(b"")
        monkeypatch.setattr("metaproc.adapters.codex_cli.sys.platform", "darwin")
        monkeypatch.setattr("metaproc.adapters.codex_cli._MACOS_MANAGED_PREFERENCES_DIR", managed)
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert "CODEX_API_KEY" not in out

    def test_bridges_when_no_stored_login(self, tmp_path: Path) -> None:
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert out["CODEX_API_KEY"] == "sk-openai"
        assert out["OPENAI_API_KEY"] == "sk-openai"

    def test_stored_auth_json_keeps_its_login(self, tmp_path: Path) -> None:
        # A ChatGPT login keeps its subscription billing; Codex would let the
        # env key outrank it.
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert "CODEX_API_KEY" not in out

    def test_explicit_codex_home_is_the_one_checked(self, tmp_path: Path) -> None:
        codex_home = tmp_path / "alt"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        out = self.adapter.prepare_env(self._env(tmp_path, CODEX_HOME=str(codex_home)), {})
        assert "CODEX_API_KEY" not in out

    def test_operator_codex_api_key_is_left_alone(self, tmp_path: Path) -> None:
        out = self.adapter.prepare_env(self._env(tmp_path, CODEX_API_KEY="sk-codex"), {})
        assert out["CODEX_API_KEY"] == "sk-codex"

    def test_env_example_placeholder_is_dropped_then_bridged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        env = self._env(tmp_path, CODEX_API_KEY="REPLACE_WITH_CODEX_API_KEY")
        with caplog.at_level(logging.WARNING, logger="metaproc.adapters.codex_cli"):
            out = self.adapter.prepare_env(env, {})
        assert out["CODEX_API_KEY"] == "sk-openai"
        assert any("placeholder" in r.getMessage() for r in caplog.records)

    def test_placeholder_is_dropped_even_with_a_stored_login(self, tmp_path: Path) -> None:
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        env = self._env(tmp_path, CODEX_API_KEY="REPLACE_WITH_CODEX_API_KEY")
        out = self.adapter.prepare_env(env, {})
        assert "CODEX_API_KEY" not in out

    def test_no_openai_key_means_no_bridge(self, tmp_path: Path) -> None:
        out = self.adapter.prepare_env({"HOME": str(tmp_path)}, {})
        assert "CODEX_API_KEY" not in out

    @pytest.mark.parametrize("store", ["keyring", "auto"])
    def test_keyring_store_in_config_toml_blocks_the_bridge(
        self, tmp_path: Path, store: str
    ) -> None:
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "config.toml").write_text(
            f'cli_auth_credentials_store = "{store}"\n'
        )
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert "CODEX_API_KEY" not in out

    def test_file_store_in_config_toml_allows_the_bridge(self, tmp_path: Path) -> None:
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert out["CODEX_API_KEY"] == "sk-openai"

    def test_config_override_keyring_store_blocks_the_bridge(self, tmp_path: Path) -> None:
        cfg: dict[str, object] = {"config_overrides": {"cli_auth_credentials_store": "keyring"}}
        out = self.adapter.prepare_env(self._env(tmp_path), cfg)
        assert "CODEX_API_KEY" not in out

    def test_unreadable_config_toml_blocks_the_bridge(self, tmp_path: Path) -> None:
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "config.toml").write_text("not = [valid toml\n")
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert "CODEX_API_KEY" not in out

    def test_oversized_config_toml_blocks_the_bridge(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("metaproc.adapters.codex_cli._MAX_CODEX_CONFIG_BYTES", 32)
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "config.toml").write_text("# " + "x" * 50, encoding="utf-8")
        out = self.adapter.prepare_env(self._env(tmp_path), {})
        assert "CODEX_API_KEY" not in out

    def test_relative_codex_home_withholds_the_bridge(self, tmp_path: Path) -> None:
        out = self.adapter.prepare_env(
            self._env(tmp_path, CODEX_HOME="relative-codex"), {"working_directory": str(tmp_path)}
        )
        assert "CODEX_API_KEY" not in out
        assert out["CODEX_HOME"] == "relative-codex"


class TestCodexBridgeFollowsStepEnv:
    """Launchers apply a step's `env` after prepare_env; the bridge decides on the merged env."""

    def setup_method(self) -> None:
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    @pytest.fixture(autouse=True)
    def _isolated(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        _isolate_codex_config_layers(monkeypatch, tmp_path)

    def _prepared(self, tmp_path: Path) -> dict[str, str]:
        env = self.adapter.prepare_env({"HOME": str(tmp_path), "OPENAI_API_KEY": "sk-openai"}, {})
        assert env["CODEX_API_KEY"] == "sk-openai"
        return env

    def test_step_codex_home_with_a_login_withdraws_the_bridge(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        step_home = tmp_path / "step-codex"
        step_home.mkdir()
        (step_home / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        env = self._prepared(tmp_path)
        with caplog.at_level(logging.INFO, logger="metaproc.adapters.codex_cli"):
            env.update({"CODEX_HOME": str(step_home)})
        assert "CODEX_API_KEY" not in env
        assert env["CODEX_HOME"] == str(step_home)
        assert any("withdrew" in r.getMessage() for r in caplog.records)

    def test_step_codex_home_with_keyring_store_withdraws_the_bridge(self, tmp_path: Path) -> None:
        step_home = tmp_path / "step-codex"
        step_home.mkdir()
        (step_home / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
        env = self._prepared(tmp_path)
        env["CODEX_HOME"] = str(step_home)
        assert "CODEX_API_KEY" not in env

    def test_step_codex_home_without_a_login_keeps_the_bridge(self, tmp_path: Path) -> None:
        step_home = tmp_path / "step-codex"
        step_home.mkdir()
        env = self._prepared(tmp_path)
        env.update({"CODEX_HOME": str(step_home)})
        assert env["CODEX_API_KEY"] == "sk-openai"

    def test_relative_step_codex_home_withdraws_the_bridge(self, tmp_path: Path) -> None:
        env = self._prepared(tmp_path)
        env.update({"CODEX_HOME": "relative-codex"})
        assert "CODEX_API_KEY" not in env
        assert env["CODEX_HOME"] == "relative-codex"

    def test_step_access_token_withdraws_the_bridge(self, tmp_path: Path) -> None:
        env = self._prepared(tmp_path)
        env.update({"CODEX_ACCESS_TOKEN": "pat"})
        assert "CODEX_API_KEY" not in env

    def test_step_openai_api_key_is_the_one_bridged(self, tmp_path: Path) -> None:
        env = self._prepared(tmp_path)
        env.update({"OPENAI_API_KEY": "sk-step"})
        assert env["CODEX_API_KEY"] == "sk-step"

    def test_step_blank_openai_api_key_withdraws_the_bridge(self, tmp_path: Path) -> None:
        env = self._prepared(tmp_path)
        env.update({"OPENAI_API_KEY": ""})
        assert "CODEX_API_KEY" not in env

    @pytest.mark.parametrize("value", ["", "sk-step-codex"])
    def test_step_codex_api_key_is_left_as_written(self, tmp_path: Path, value: str) -> None:
        env = self._prepared(tmp_path)
        env.update({"CODEX_API_KEY": value})
        assert env["CODEX_API_KEY"] == value
        env.update({"CODEX_HOME": str(tmp_path / "elsewhere")})
        assert env["CODEX_API_KEY"] == value

    def test_unrelated_step_env_keeps_the_bridge(self, tmp_path: Path) -> None:
        env = self._prepared(tmp_path)
        env.update({"FOO": "bar"})
        env["METAPROC_LANE_ID"] = "lane"
        assert env["CODEX_API_KEY"] == "sk-openai"

    def test_pickles_and_copies_as_a_plain_dict(self, tmp_path: Path) -> None:
        env = self._prepared(tmp_path)
        restored = pickle.loads(pickle.dumps(env))  # noqa: S301 -- round-trips our own object
        assert type(restored) is dict
        assert restored == dict(env)

    def test_launch_step_applies_step_env_before_codex_sees_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Through the real launcher: a step CODEX_HOME with a ChatGPT login keeps that login."""
        step_home = tmp_path / "step-codex"
        step_home.mkdir()
        (step_home / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
        seen: dict[str, dict[str, str]] = {}

        class _Proc:
            pid = 4242

        def _popen(_cmd: list[str], **kwargs: Any) -> _Proc:
            seen["env"] = dict(kwargs["env"])
            return _Proc()

        monkeypatch.setattr("metaproc.engine.runtime.subprocess.Popen", _popen)
        monkeypatch.setattr("metaproc.adapters.codex_cli._codex_version_drift", lambda: None)
        logs = tmp_path / "logs"
        logs.mkdir()
        engine_runtime.launch_step(
            "step",
            "prompt",
            logs,
            logs / "step.log",
            "codex-cli",
            {"permission_mode": "default"},
            {},
            "item",
            step_env={"CODEX_HOME": str(step_home)},
        )
        assert seen["env"]["CODEX_HOME"] == str(step_home)
        assert "CODEX_API_KEY" not in seen["env"]


class TestCodexCheckAuthPrecedence:
    """check_auth names the credential Codex will use: CODEX_API_KEY, auth.json, then the bridge."""

    def setup_method(self) -> None:
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    @pytest.fixture(autouse=True)
    def _isolated(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "metaproc.adapters.codex_cli.verify_codex_version",
            lambda **_kwargs: PINNED_CODEX_CLI_VERSION,
        )
        monkeypatch.setattr("metaproc.adapters.codex_cli.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        _isolate_codex_config_layers(monkeypatch, tmp_path)

    def test_access_token_wins_over_auth_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CODEX_ACCESS_TOKEN", "pat")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        (tmp_path / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        status = self.adapter.check_auth()
        assert status.auth_mode == "access-token"
        assert status.credentials_found is True
        assert "do not pass OPENAI_API_KEY" in status.details

    def test_codex_api_key_wins_over_access_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_ACCESS_TOKEN", "pat")
        monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
        assert self.adapter.check_auth().auth_mode == "api-key"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_codex_api_key_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, blank: str
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", blank)
        (tmp_path / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        assert self.adapter.check_auth().auth_mode == "interactive-login"

    def test_blank_openai_api_key_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "  ")
        assert self.adapter.check_auth().auth_mode == "none"

    def test_openai_api_key_with_system_keyring_store_is_unknown(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        (tmp_path / "etc-codex" / "config.toml").write_text(
            'cli_auth_credentials_store = "keyring"\n'
        )
        status = self.adapter.check_auth()
        assert status.auth_mode == "unknown"
        assert status.credentials_found is False
        assert "etc-codex" in status.details

    def test_auth_json_wins_over_openai_api_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        (tmp_path / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        status = self.adapter.check_auth()
        assert status.auth_mode == "interactive-login"
        assert "OPENAI_API_KEY is set but Metaproc defers to auth.json" in status.details

    @pytest.mark.parametrize(
        "payload, missing",
        [
            ({"auth_mode": "chatgpt"}, "tokens.access_token"),
            ({"tokens": {"access_token": " "}}, "tokens.access_token"),
            ({"auth_mode": "apikey"}, "OPENAI_API_KEY"),
            ({"auth_mode": "apikey", "OPENAI_API_KEY": " "}, "OPENAI_API_KEY"),
        ],
    )
    def test_incomplete_stored_login_is_unverified(
        self, tmp_path: Path, payload: dict[str, object], missing: str
    ) -> None:
        (tmp_path / "auth.json").write_text(json.dumps(payload))
        status = self.adapter.check_auth()
        assert status.auth_mode == "unknown"
        assert status.credentials_found is False
        assert missing in status.details

    def test_codex_api_key_wins_over_auth_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
        (tmp_path / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        status = self.adapter.check_auth()
        assert status.auth_mode == "api-key"
        assert "CODEX_API_KEY" in status.details

    def test_codex_api_key_placeholder_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "REPLACE_WITH_CODEX_API_KEY")
        (tmp_path / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        status = self.adapter.check_auth()
        assert status.auth_mode == "interactive-login"

    def test_openai_api_key_with_keyring_store_is_unknown(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
        status = self.adapter.check_auth()
        assert status.auth_mode == "unknown"
        assert status.credentials_found is False
        assert "keyring" in status.details

    @pytest.mark.parametrize("with_auth_file", [False, True])
    def test_dry_auth_reports_keyring_unverified_without_an_api_key(
        self, tmp_path: Path, with_auth_file: bool
    ) -> None:
        (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
        if with_auth_file:
            (tmp_path / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)
        status = self.adapter.check_auth()
        assert status.auth_mode == "unknown"
        assert status.credentials_found is False
        assert "keyring" in status.details

    def test_nothing_configured_is_none(self) -> None:
        status = self.adapter.check_auth()
        assert status.auth_mode == "none"
        assert status.credentials_found is False


class TestCodexApiKeyRedaction:
    """Explicit Codex credentials are masked wherever Metaproc masks secrets."""

    @pytest.mark.parametrize("var", [MetaprocEnv.CODEX_API_KEY, MetaprocEnv.CODEX_ACCESS_TOKEN])
    def test_registered_as_secret(self, var: MetaprocEnv) -> None:
        assert var.kind == "SECRET"
        assert var in SECRET_VARS

    def test_invocation_sidecar_redacts_it(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "invocation.json"
        write_invocation_sidecar(
            target=sidecar,
            argv=["codex", "exec"],
            env={"CODEX_API_KEY": "sk-codex-secret-value"},
            cwd=tmp_path,
        )
        text = sidecar.read_text()
        assert "sk-codex-secret-value" not in text
        assert "CODEX_API_KEY" in text

    def test_command_diagnostics_redact_it(self) -> None:
        detail = redact_diagnostic(
            "codex failed with key sk-codex-distinct-1234567890",
            env={"CODEX_API_KEY": "sk-codex-distinct-1234567890"},
        )
        assert "sk-codex-distinct-1234567890" not in detail
        assert "[redacted]" in detail

    def test_resource_context_dump_redacts_it(self) -> None:
        shown = _redact_resource_env(
            "CODEX_API_KEY", "sk-codex-secret-value", secret_targets=frozenset()
        )
        assert "sk-codex-secret-value" not in shown


# ── Codex --assert-model (auth-check) ───────────────────────────


def _write_rollout(codex_home: Path, thread_id: str, model: str) -> Path:
    day = codex_home / "sessions" / "2026" / "11" / "18"
    day.mkdir(parents=True)
    rollout = day / f"rollout-2026-11-18T02-35-25-{thread_id}.jsonl"
    rollout.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": thread_id}})
        + "\n"
        + json.dumps({"type": "turn_context", "payload": {"model": model, "effort": "medium"}})
        + "\n"
    )
    return rollout


_THREAD_ID = "01a1ec82-4ce4-7883-b842-8ecab3846d7b"


class TestCodexRolloutModel:
    def test_thread_id_from_exec_json(self) -> None:
        assert codex_thread_id(_CODEX_0166_EXEC_JSON) == _THREAD_ID
        assert codex_thread_id('{"type":"turn.started"}\n') is None

    def test_reads_turn_context_model(self, tmp_path: Path) -> None:
        _write_rollout(tmp_path, _THREAD_ID, "gpt-6.1-sol")
        assert codex_rollout_model(tmp_path, _THREAD_ID) == "gpt-6.1-sol"

    def test_stops_after_the_first_model_record(self, tmp_path: Path) -> None:
        rollout = _write_rollout(tmp_path, _THREAD_ID, "gpt-6.1-sol")
        rollout.write_bytes(rollout.read_bytes() + b"\xff\n")
        assert codex_rollout_model(tmp_path, _THREAD_ID) == "gpt-6.1-sol"

    def test_recent_rollout_does_not_scan_session_history(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recent = tmp_path / "sessions" / datetime.now(UTC).strftime("%Y/%m/%d")
        recent.mkdir(parents=True)
        rollout = recent / f"rollout-recent-{_THREAD_ID}.jsonl"
        rollout.write_text(
            '{"type":"turn_context","payload":{"model":"gpt-6.1-sol"}}\n', encoding="utf-8"
        )

        def reject_history_scan(_path: Path, _pattern: str) -> None:
            pytest.fail("A recent rollout should not require a recursive scan")

        monkeypatch.setattr(Path, "rglob", reject_history_scan)
        assert codex_rollout_model(tmp_path, _THREAD_ID) == "gpt-6.1-sol"

    @pytest.mark.parametrize("compressed", [False, True])
    def test_ignores_model_after_an_oversized_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compressed: bool
    ) -> None:
        monkeypatch.setattr("metaproc.adapters.codex_cli._MAX_ROLLOUT_LINE_BYTES", 128)
        rollout = _write_rollout(tmp_path, _THREAD_ID, "gpt-6.1-sol")
        data = b" " * 129 + b"\n" + rollout.read_bytes()
        if compressed:
            rollout.with_suffix(".jsonl.zst").write_bytes(zstd.compress(data))
            rollout.unlink()
        else:
            rollout.write_bytes(data)
        assert codex_rollout_model(tmp_path, _THREAD_ID) is None

    def test_missing_rollout_is_none(self, tmp_path: Path) -> None:
        assert codex_rollout_model(tmp_path, _THREAD_ID) is None

    @pytest.mark.parametrize("thread_id", ["", "*", "../x", "a?b"])
    def test_unsafe_thread_id_is_refused(self, tmp_path: Path, thread_id: str) -> None:
        _write_rollout(tmp_path, _THREAD_ID, "gpt-6.1-sol")
        assert codex_rollout_model(tmp_path, thread_id) is None


class TestCodexAssertModel:
    """codex exec reports no served model; the assertion reads the requested one, labelled."""

    def test_rollout_model_passes_and_is_labelled_requested(self, tmp_path: Path) -> None:
        _write_rollout(tmp_path, _THREAD_ID, "gpt-6.1-sol")
        ok, msg = _evaluate_model_assertion(
            "codex-cli",
            _CODEX_0166_EXEC_JSON,
            "gpt-6.1",
            "codex-cli",
            env={"CODEX_HOME": str(tmp_path)},
            cmd=["codex", "-m", "gpt-5.5", "exec", "--json", "-"],
        )
        assert ok, msg
        assert "requested model 'gpt-6.1-sol'" in msg
        assert "session rollout" in msg
        assert "reports no served model" in msg

    def test_rollout_model_mismatch_fails(self, tmp_path: Path) -> None:
        _write_rollout(tmp_path, _THREAD_ID, "gpt-6.1-sol")
        ok, msg = _evaluate_model_assertion(
            "codex-cli",
            _CODEX_0166_EXEC_JSON,
            "bogus-xyz-nomatch",
            "codex-cli",
            env={"CODEX_HOME": str(tmp_path)},
            cmd=None,
        )
        assert not ok
        assert "does not match" in msg

    def test_argv_alone_is_not_verifiable(self, tmp_path: Path) -> None:
        # The probe's own `-m` is what Metaproc asked for; matching it would be a tautology.
        ok, msg = _evaluate_model_assertion(
            "codex-cli",
            _CODEX_0166_EXEC_JSON,
            "gpt-6.1-sol",
            "codex-cli",
            env={"CODEX_HOME": str(tmp_path)},
            cmd=["/bin/sh", "-c", "x", "metaproc-codex", "p", "codex", "-m", "gpt-6.1-sol", "exec"],
        )
        assert not ok
        assert "cannot verify" in msg
        assert "-m 'gpt-6.1-sol'" in msg
        assert "reports no served model" in msg

    def test_compressed_rollout_is_read(self, tmp_path: Path) -> None:
        plain = _write_rollout(tmp_path, _THREAD_ID, "gpt-6.1-sol")
        compressed = plain.with_name(plain.name + ".zst")
        compressed.write_bytes(zstd.compress(plain.read_bytes()))
        plain.unlink()
        ok, msg = _evaluate_model_assertion(
            "codex-cli",
            _CODEX_0166_EXEC_JSON,
            "gpt-6.1",
            "codex-cli",
            env={"CODEX_HOME": str(tmp_path)},
            cmd=None,
        )
        assert ok, msg
        assert "requested model 'gpt-6.1-sol'" in msg

    def test_corrupt_compressed_rollout_is_skipped(self, tmp_path: Path) -> None:
        day = tmp_path / "sessions" / "2026" / "11" / "18"
        day.mkdir(parents=True)
        (day / f"rollout-2026-11-18T02-35-25-{_THREAD_ID}.jsonl.zst").write_bytes(b"not zstd")
        assert codex_rollout_model(tmp_path, _THREAD_ID) is None

    def test_nothing_found_fails(self, tmp_path: Path) -> None:
        ok, msg = _evaluate_model_assertion(
            "codex-cli",
            '{"type":"turn.completed","usage":{}}\n',
            "gpt",
            "codex-cli",
            env={"CODEX_HOME": str(tmp_path)},
            cmd=None,
        )
        assert not ok
        assert "cannot verify" in msg
        assert "recorded no requested model" in msg

    def test_live_check_threads_env_and_cmd_through(self, tmp_path: Path) -> None:
        """End to end through `_run_live_check` with a stubbed codex subprocess."""
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        _write_rollout(codex_home, _THREAD_ID, CODEX_DEFAULT_MODEL)
        results = self._live_check(codex_home)

        assert all(ok for ok, _ in results), results
        assert any(f"requested model {CODEX_DEFAULT_MODEL!r}" in msg for _, msg in results)

    def test_live_check_without_rollout_fails(self, tmp_path: Path) -> None:
        """The probe passes `-m`, but with no rollout the assertion must not pass on it."""
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        results = self._live_check(codex_home)

        assert not all(ok for ok, _ in results), results
        assert any("cannot verify" in msg and "-m " in msg for ok, msg in results if not ok)

    @staticmethod
    def _live_check(codex_home: Path) -> list[tuple[bool, str]]:
        adapter = CodexCliAdapter()
        auth_status = AuthStatus(
            adapter_type="codex-cli",
            cli_found=True,
            cli_path="/usr/bin/codex",
            credentials_found=True,
            auth_mode="api-key",
            details="",
            setup_hint="",
        )
        proc = subprocess.CompletedProcess(
            args=["codex"], returncode=0, stdout=_CODEX_0166_EXEC_JSON, stderr=""
        )
        with (
            patch.dict("metaproc.commands.auth_check.ADAPTER_REGISTRY", {"codex-cli": adapter}),
            patch.object(adapter, "check_auth", return_value=auth_status),
            patch("metaproc.adapters.codex_cli._codex_version_drift", return_value=None),
            patch("metaproc.commands.auth_check.run_command_in_process_group", return_value=proc),
            patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}),
        ):
            return _run_live_check("codex-cli", None, timeout_s=5, assert_model="gpt")


# ── Cost: cache writes on rows without a write rate ─────────────


class TestCodexCacheWriteCost:
    _STATS_USAGE: ClassVar[dict[str, int]] = {
        "input_tokens": 3_000_000,
        "cached_input_tokens": 1_000_000,
        "cache_write_input_tokens": 1_000_000,
        "output_tokens": 0,
    }

    @staticmethod
    def _pricing(provider: str, **rates: float) -> dict[str, dict[str, Any]]:
        return {"m": {"provider": provider, "actual_price": {"output_per_1m": 0.0, **rates}}}

    def _stats(self) -> UsageStats:
        stats = sum_codex_usage({"type": "turn.completed", "usage": self._STATS_USAGE})
        stats.model = "m"
        return stats

    def test_openai_row_without_write_rate_bills_writes_at_input_rate(self) -> None:
        pricing = self._pricing("openai", input_per_1m=2.0, cache_read_per_1m=0.1)
        # 1M uncached + 1M written at $2, 1M cached at $0.10: as before the split.
        assert compute_cost(self._stats(), pricing) == pytest.approx(4.1)

    def test_openai_row_with_write_rate_uses_it(self) -> None:
        pricing = self._pricing(
            "openai", input_per_1m=2.0, cache_read_per_1m=0.1, cache_write_per_1m=2.5
        )
        assert compute_cost(self._stats(), pricing) == pytest.approx(4.6)

    def test_anthropic_row_keeps_its_written_rate(self) -> None:
        # The Claude path is unchanged: an absent write rate still prices at 0.
        pricing = self._pricing("anthropic", input_per_1m=2.0, cache_read_per_1m=0.1)
        assert compute_cost(self._stats(), pricing) == pytest.approx(2.1)

    def test_packaged_gpt_5_5_row_is_unchanged(self) -> None:
        stats = sum_codex_usage(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1_000_000,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 1_000_000,
                },
            }
        )
        stats.model = "gpt-5.5"
        assert compute_cost(stats, load_pricing()) == pytest.approx(35.0, abs=0.01)
