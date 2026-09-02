"""Tests for metaproc.adapters.codex — CodexCliAdapter."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from metaproc.adapters.base import Adapter, AuthCapableCliAdapter
from metaproc.adapters.codex import (
    CODEX_CREDS_ENV_VAR,
    PINNED_CODEX_CLI_VERSION,
    CodexCliAdapter,
    CodexCliVersionMismatch,
    verify_codex_version,
)
from metaproc.settings import CODEX_DEFAULT_MODEL

_VALID_OAUTH_PAYLOAD = json.dumps(
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
            "metaproc.adapters.codex._codex_version_drift",
            lambda: "Codex CLI version mismatch: pinned='0.147.0', actual='0.135.0'",
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
        assert cmd[cmd.index("-a") + 1] == "on-failure"

    def test_plan_permission_mode(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, {"permission_mode": "plan"})
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"
        assert cmd[cmd.index("-a") + 1] == "untrusted"

    def test_top_level_flags_precede_exec(self, tmp_path: Path):
        """-a / --search / --sandbox / -m must be top-level-only; before `exec`."""
        cmd = self._cmd(
            tmp_path,
            {"permission_mode": "plan", "tools": ["WebSearch"], "model": "gpt-5.4"},
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

    def test_model_validation_falls_back_on_unknown(self, tmp_path: Path):
        # Adapter logs a warning and falls back to CODEX_DEFAULT_MODEL rather
        # than emitting an invalid -m value (matches claude/gemini behavior).
        cmd = self._cmd(tmp_path, {"permission_mode": "default", "model": "gpt-bogus"})
        assert cmd[cmd.index("-m") + 1] == CODEX_DEFAULT_MODEL

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
                    "model": "gpt-5.4",
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


class TestCodexCheckAuth:
    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    @pytest.fixture(autouse=True)
    def _working_version_probe(self, monkeypatch):
        monkeypatch.setattr(
            "metaproc.adapters.codex.verify_codex_version",
            lambda **_kwargs: PINNED_CODEX_CLI_VERSION,
        )

    def test_no_cli_reports_none(self, monkeypatch):
        monkeypatch.setattr("metaproc.adapters.codex.shutil.which", lambda _: None)
        status = self.adapter.check_auth()
        assert status.cli_found is False
        assert status.auth_mode == "none"
        assert "not found" in status.details.lower()

    def test_openai_api_key_wins(self, monkeypatch):
        monkeypatch.setattr("metaproc.adapters.codex.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        status = self.adapter.check_auth()
        assert status.auth_mode == "api-key"
        assert status.credentials_found is True
        assert "OPENAI_API_KEY" in status.details

    def test_chatgpt_oauth_session(self, monkeypatch, tmp_path):
        monkeypatch.setattr("metaproc.adapters.codex.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "auth.json").write_text(_VALID_OAUTH_PAYLOAD)

        status = self.adapter.check_auth()
        assert status.auth_mode == "interactive-login"
        assert status.credentials_found is True

    def test_apikey_auth_json_classified_as_api_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr("metaproc.adapters.codex.shutil.which", lambda _: "/usr/bin/codex")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "auth.json").write_text(
            json.dumps({"tokens": {"auth_mode": "apikey", "access_token": "sk"}})
        )

        status = self.adapter.check_auth()
        assert status.auth_mode == "api-key"

    def test_no_creds_reports_none_with_setup_hint(self, monkeypatch, tmp_path):
        monkeypatch.setattr("metaproc.adapters.codex.shutil.which", lambda _: "/usr/bin/codex")
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
        monkeypatch.setattr("metaproc.adapters.codex.shutil.which", lambda _: "/usr/bin/codex")
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
            "metaproc.adapters.codex.shutil.which",
            lambda _: "/usr/local/bin/codex",
        )

        def fail_probe(**_kwargs):
            raise CodexCliVersionMismatch("launcher child executable is missing: ENOENT")

        monkeypatch.setattr("metaproc.adapters.codex.verify_codex_version", fail_probe)

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

    monkeypatch.setattr("metaproc.adapters.codex.subprocess.run", failed_run)

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

    monkeypatch.setattr("metaproc.adapters.codex.subprocess.run", successful_run)

    assert verify_codex_version() == PINNED_CODEX_CLI_VERSION


def test_verify_codex_version_rejects_different_version(monkeypatch):
    def mismatched_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["codex", "--version"],
            returncode=0,
            stdout="codex-cli 0.146.0-alpha.3.1\n",
            stderr="",
        )

    monkeypatch.setattr("metaproc.adapters.codex.subprocess.run", mismatched_run)

    with pytest.raises(
        CodexCliVersionMismatch,
        match=r"pinned='0\.147\.0', actual='0\.146\.0-alpha\.3\.1'",
    ):
        verify_codex_version()


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
