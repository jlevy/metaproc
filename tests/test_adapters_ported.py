"""Unit tests for adapters — ported from example_plugin.

Tests cover:
  - Registry lookup (get_adapter)
  - ClaudeCodeCliAdapter.build_command / prepare_env
  - GeminiCliAdapter.build_command / prepare_env
  - parse_result_event for both adapters
  - derive_variant and _sanitize_tag
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from metaproc.adapters.claude_code import ClaudeCodeCliAdapter
from metaproc.adapters.gemini import GeminiCliAdapter, _build_gemini_flags
from metaproc.adapters.pi_cli import PiCliAdapter
from metaproc.adapters.registry import (
    ADAPTER_REGISTRY,
    _sanitize_tag,
    derive_variant,
    get_adapter,
)
from metaproc.settings import GEMINI_DEFAULT_NATIVE_SETTINGS

# ── Registry tests ────────────────────────────────────────────────


class TestGetAdapter:
    def test_returns_claude_adapter(self) -> None:
        adapter = get_adapter("claude-code-cli")
        assert isinstance(adapter, ClaudeCodeCliAdapter)

    def test_returns_gemini_adapter(self) -> None:
        adapter = get_adapter("gemini-cli")
        assert isinstance(adapter, GeminiCliAdapter)

    def test_raises_on_unknown_type(self) -> None:
        with pytest.raises(ValueError, match="unknown adapter type"):
            get_adapter("aider-cli")

    def test_registry_has_expected_keys(self) -> None:
        assert set(ADAPTER_REGISTRY) == {
            "claude-code-cli",
            "codex-cli",
            "gemini-cli",
            "pi-cli",
        }


# ── ClaudeCodeCliAdapter ──────────────────────────────────────────


class TestClaudeCodeCliAdapter:
    def setup_method(self) -> None:
        self.adapter = ClaudeCodeCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]
        self.prompt_file = Path("/tmp/prompt.txt")  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_adapter_type(self) -> None:
        assert self.adapter.adapter_type == "claude-code-cli"

    # Base config with required permission_mode for batch mode
    _BASE = {"permission_mode": "bypassPermissions"}

    def _cfg(self, **overrides: object) -> dict[str, object]:
        return {**self._BASE, **overrides}

    def test_missing_permission_mode_raises(self) -> None:

        with pytest.raises(ValueError, match="permission_mode"):
            self.adapter.build_command(self.prompt_file, {}, {})

    def test_minimal_command(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, self._cfg(), {})
        assert cmd[:3] == ["claude", "-p", f"@{self.prompt_file}"]
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--verbose" in cmd
        assert "--no-session-persistence" in cmd
        assert "--permission-mode" in cmd

    def test_model_flag(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, self._cfg(model="sonnet"), {})
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"

    def test_effort_flag(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, self._cfg(effort="high"), {})
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "high"

    def test_permission_mode_flag(self) -> None:
        # bypassPermissions stays as-is, but pairs with
        # --dangerously-skip-permissions and --add-dir <cwd> for Claude Code
        # 2.1.123+ compatibility (see claude_code.py comment).
        cmd = self.adapter.build_command(
            self.prompt_file, self._cfg(permission_mode="bypassPermissions"), {}
        )
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "bypassPermissions"
        assert "--dangerously-skip-permissions" in cmd
        assert "--add-dir" in cmd

    def test_permission_mode_passes_through_for_non_bypass(self) -> None:
        cmd = self.adapter.build_command(
            self.prompt_file, self._cfg(permission_mode="acceptEdits"), {}
        )
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "acceptEdits"
        assert "--dangerously-skip-permissions" not in cmd
        assert "--add-dir" not in cmd

    def test_tools_flag(self) -> None:
        cmd = self.adapter.build_command(
            self.prompt_file, self._cfg(tools=["Read", "Write", "Bash"]), {}
        )
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == "Read,Write,Bash"

    def test_max_budget_flag(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, self._cfg(max_budget_usd=5), {})
        idx = cmd.index("--max-budget-usd")
        assert cmd[idx + 1] == "5"

    def test_worktree_flag(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, self._cfg(worktree=True), {})
        assert "--worktree" in cmd

    def test_no_verbose_when_false(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, self._cfg(verbose=False), {})
        assert "--verbose" not in cmd

    def test_no_session_persistence_when_false(self) -> None:
        cmd = self.adapter.build_command(
            self.prompt_file, self._cfg(no_session_persistence=False), {}
        )
        assert "--no-session-persistence" not in cmd

    def test_append_system_prompt(self) -> None:
        cmd = self.adapter.build_command(
            self.prompt_file,
            self._cfg(append_system_prompt="Be concise."),
            {},
        )
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "Be concise."

    def test_system_prompt_resolves_variables(self) -> None:
        cmd = self.adapter.build_command(
            self.prompt_file,
            self._cfg(append_system_prompt="Date is {{DATE}}"),
            {"DATE": "2026-03-24"},
        )
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "Date is 2026-03-24"

    def test_full_config_golden(self) -> None:
        """Golden test: all config keys set. Asserts structure but skips
        the absolute --add-dir <cwd> path (test runner can be in any dir)."""
        config: dict[str, object] = {
            "model": "opus",
            "effort": "high",
            "permission_mode": "bypassPermissions",
            "tools": ["Read", "Write"],
            "max_budget_usd": 10,
            "output_format": "stream-json",
            "verbose": True,
            "no_session_persistence": True,
            "append_system_prompt": "Focus on {{TICKER}}",
            "worktree": True,
        }
        cmd = self.adapter.build_command(self.prompt_file, config, {"TICKER": "AAPL"})
        # Drop the --add-dir <cwd> pair before comparing to make the golden
        # cwd-independent.
        if "--add-dir" in cmd:
            i = cmd.index("--add-dir")
            cmd = cmd[:i] + cmd[i + 2 :]
        assert cmd == [
            "claude",
            "-p",
            f"@{self.prompt_file}",
            "--model",
            "opus",
            "--effort",
            "high",
            "--permission-mode",
            "bypassPermissions",
            "--dangerously-skip-permissions",
            "--allowedTools",
            "Bash Write Edit Read Glob Grep WebSearch WebFetch Task TodoWrite NotebookEdit",
            "--setting-sources",
            "user",
            "--tools",
            "Read,Write",
            "--max-budget-usd",
            "10",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--append-system-prompt",
            "Focus on AAPL",
            "--worktree",
        ]

    def test_prepare_env_strips_claudecode(self) -> None:
        env = {"PATH": "/usr/bin", "CLAUDECODE": "1", "HOME": "/workspace/user"}
        result = self.adapter.prepare_env(env, {})
        assert "CLAUDECODE" not in result
        assert result["PATH"] == "/usr/bin"
        assert result["HOME"] == "/workspace/user"

    def test_prepare_env_no_claudecode_is_noop_without_bypass(self) -> None:
        # prepare_env scrubs CLAUDECODE/ENTRYPOINT and gates IS_SANDBOX=1
        # on permission_mode=bypassPermissions. Without bypass, env passes
        # through unchanged.
        env = {"PATH": "/usr/bin"}
        result = self.adapter.prepare_env(env, {})
        assert result == {"PATH": "/usr/bin"}

    def test_prepare_env_sets_sandbox_flag_only_for_bypass(self) -> None:
        env = {"PATH": "/usr/bin"}
        result = self.adapter.prepare_env(env, {"permission_mode": "bypassPermissions"})
        assert result == {"PATH": "/usr/bin", "IS_SANDBOX": "1"}

        result_default = self.adapter.prepare_env(env, {"permission_mode": "default"})
        assert "IS_SANDBOX" not in result_default

    def test_prepare_env_native_settings_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """native_settings is not yet supported for Claude -- should warn."""
        env = {"PATH": "/usr/bin"}
        settings = {"permissions": {"allow": ["Read"]}}
        with caplog.at_level("WARNING"):
            result = self.adapter.prepare_env(env, {"native_settings": settings})
        assert "native_settings is not yet supported" in caplog.text
        # No env var injected
        assert "CLAUDE_CODE_SETTINGS_PATH" not in result

    def test_working_directory_returns_none(self) -> None:
        assert self.adapter.working_directory({}) is None

    def test_parse_result_event_valid(self) -> None:
        line = '{"type": "result", "status": "success", "tokens": 1234}'
        result = self.adapter.parse_result_event(line)
        assert result is not None
        assert result["type"] == "result"
        assert result["status"] == "success"

    def test_parse_result_event_non_result(self) -> None:
        line = '{"type": "assistant", "content": "hello"}'
        assert self.adapter.parse_result_event(line) is None

    def test_parse_result_event_invalid_json(self) -> None:
        assert self.adapter.parse_result_event("not json") is None

    def test_parse_result_event_empty(self) -> None:
        assert self.adapter.parse_result_event("") is None


# ── GeminiCliAdapter ──────────────────────────────────────────────


class TestGeminiCliAdapter:
    def setup_method(self) -> None:
        self.adapter = GeminiCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]
        self.prompt_file = Path("/tmp/prompt.txt")  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_adapter_type(self) -> None:
        assert self.adapter.adapter_type == "gemini-cli"

    def test_minimal_command(self) -> None:
        self.prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(self.prompt_file, {}, {})
        assert cmd[:5] == [
            "/bin/sh",
            "-c",
            'prompt_file=$1; shift; exec "$@" < "$prompt_file"',
            "metaproc-gemini",
            str(self.prompt_file),
        ]
        assert cmd[5:8] == ["gemini", "-p", ""]
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        # Gemini should NOT have --verbose or --no-session-persistence
        assert "--verbose" not in cmd
        assert "--no-session-persistence" not in cmd

    def test_model_flag_uses_dash_m(self) -> None:
        self.prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(self.prompt_file, {"model": "flash"}, {})
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "flash"

    def test_existing_prompt_file_is_referenced(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(prompt_file, {}, {})
        assert str(prompt_file) == cmd[4]
        assert f"@{prompt_file}" not in cmd

    def test_missing_prompt_file_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="prompt file does not exist"):
            self.adapter.build_command(tmp_path / "missing.txt", {}, {})

    def test_dry_run_prompt_sentinel_is_renderable(self) -> None:
        cmd = self.adapter.build_command(Path("<prompt-file>"), {}, {})

        assert cmd[4] == "<prompt-file>"

    def test_bypass_permissions_maps_to_yolo(self) -> None:
        self.prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(
            self.prompt_file, {"permission_mode": "bypassPermissions"}, {}
        )
        idx = cmd.index("--approval-mode")
        assert cmd[idx + 1] == "yolo"

    def test_non_bypass_permission_mode_omitted(self) -> None:
        """Non-bypass permission modes should NOT default to yolo."""
        self.prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(self.prompt_file, {"permission_mode": "default"}, {})
        assert "--approval-mode" not in cmd

    def test_no_permission_mode_no_approval(self) -> None:
        self.prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(self.prompt_file, {}, {})
        assert "--approval-mode" not in cmd

    def test_sandbox_flag(self) -> None:
        self.prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(self.prompt_file, {"sandbox": "docker"}, {})
        idx = cmd.index("-s")
        assert cmd[idx + 1] == "docker"

    def test_tools_are_enforced_with_generated_policy(self) -> None:
        """Claude-style tool names become a strict Gemini policy boundary."""
        self.prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(self.prompt_file, {"tools": ["Read", "Write"]}, {})
        assert "--tools" not in cmd
        policy_path = Path(cmd[cmd.index("--policy") + 1])
        policy = policy_path.read_text()
        assert 'toolName = "*"' in policy
        assert 'decision = "deny"' in policy
        assert 'toolName = ["read_file", "write_file"]' in policy
        assert 'decision = "allow"' in policy

    def test_max_budget_not_passed(self) -> None:
        """Gemini has no --max-budget-usd; timeout enforcement is external."""
        self.prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(self.prompt_file, {"max_budget_usd": 5}, {})
        assert "--max-budget-usd" not in cmd

    def test_full_config_golden(self) -> None:
        """Golden test: Gemini with all relevant config keys."""
        config: dict[str, object] = {
            "model": "pro",
            "permission_mode": "bypassPermissions",
            "output_format": "stream-json",
            "sandbox": "seatbelt",
        }
        self.prompt_file.write_text("Analyze PAYX")
        cmd = self.adapter.build_command(self.prompt_file, config, {})
        assert cmd == [
            "/bin/sh",
            "-c",
            'prompt_file=$1; shift; exec "$@" < "$prompt_file"',
            "metaproc-gemini",
            str(self.prompt_file),
            "gemini",
            "-p",
            "",
            "-m",
            "pro",
            "--approval-mode",
            "yolo",
            "--output-format",
            "stream-json",
            # --skip-trust is unconditional in _build_gemini_flags: gemini-cli
            # 0.40+ exits 55 from an "untrusted" workspace, which deadlocks
            # every headless dispatch metaproc does.
            "--skip-trust",
            "-s",
            "seatbelt",
        ]

    def test_prepare_env_does_not_strip_claudecode(self) -> None:
        env = {"PATH": "/usr/bin", "CLAUDECODE": "1"}
        result = self.adapter.prepare_env(env, {})
        assert result["CLAUDECODE"] == "1"

    def test_prepare_env_sets_gemini_system_md(self) -> None:
        env = {"PATH": "/usr/bin"}
        result = self.adapter.prepare_env(env, {"append_system_prompt": "Be helpful."})
        assert "GEMINI_SYSTEM_MD" in result
        system_md_path = Path(result["GEMINI_SYSTEM_MD"])
        assert system_md_path.exists()
        assert system_md_path.read_text() == "Be helpful."
        second = self.adapter.prepare_env(env, {"append_system_prompt": "Be helpful."})
        assert Path(second["GEMINI_SYSTEM_MD"]) == system_md_path

    def test_prepare_env_no_system_prompt(self) -> None:
        env = {"PATH": "/usr/bin"}
        result = self.adapter.prepare_env(env, {})
        assert "GEMINI_SYSTEM_MD" not in result

    def test_prepare_env_default_native_settings(self) -> None:
        """Without explicit native_settings, defaults are injected."""
        env = {"PATH": "/usr/bin"}
        result = self.adapter.prepare_env(env, {})
        assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH" in result
        settings_path = Path(result["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
        assert settings_path.exists()
        written = json.loads(settings_path.read_text())
        assert written == GEMINI_DEFAULT_NATIVE_SETTINGS
        assert written["agents"]["overrides"]["generalist"]["enabled"] is False
        second = self.adapter.prepare_env(env, {})
        assert Path(second["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]) == settings_path

    def test_prepare_env_explicit_native_settings(self) -> None:
        """Explicit native_settings overrides the default."""
        env = {"PATH": "/usr/bin"}
        custom = {"agents": {"overrides": {"generalist": {"enabled": True}}}}
        result = self.adapter.prepare_env(env, {"native_settings": custom})
        settings_path = Path(result["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
        written = json.loads(settings_path.read_text())
        assert written["agents"]["overrides"]["generalist"]["enabled"] is True

    def test_prepare_env_native_settings_null_skips_injection(self) -> None:
        """native_settings: null (None) skips injection entirely."""
        env = {"PATH": "/usr/bin"}
        result = self.adapter.prepare_env(env, {"native_settings": None})
        assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH" not in result

    def test_prepare_env_does_not_inject_gemini_api_key(self) -> None:
        """GOOGLE_API_KEY is for Vertex AI Express; adapter should not remap it."""
        env = {"GOOGLE_API_KEY": "AIza-test-key"}
        result = self.adapter.prepare_env(env, {})
        assert "GEMINI_API_KEY" not in result

    def test_prepare_env_preserves_existing_gemini_api_key(self) -> None:
        env = {"GOOGLE_API_KEY": "google-key", "GEMINI_API_KEY": "gemini-key"}
        result = self.adapter.prepare_env(env, {})
        assert result["GEMINI_API_KEY"] == "gemini-key"

    def test_working_directory_returns_none(self) -> None:
        assert self.adapter.working_directory({}) is None

    def test_parse_result_event_valid(self) -> None:
        line = '{"type": "result", "status": "success"}'
        result = self.adapter.parse_result_event(line)
        assert result is not None
        assert result["type"] == "result"

    def test_parse_result_event_non_result(self) -> None:
        line = '{"type": "tool_use", "name": "read_file"}'
        assert self.adapter.parse_result_event(line) is None

    def test_parse_result_event_invalid_json(self) -> None:
        assert self.adapter.parse_result_event("not json") is None


# ── PiCliAdapter ─────────────────────────────────────────────────


class TestPiCliAdapter:
    @pytest.fixture(autouse=True)
    def _mock_pi_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("metaproc.adapters.pi_cli._resolve_pi_binary", lambda: "/usr/bin/pi")

    def setup_method(self) -> None:
        self.adapter = PiCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]
        self.prompt_file = Path("/tmp/prompt.txt")  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_adapter_type(self) -> None:
        assert self.adapter.adapter_type == "pi-cli"

    def test_minimal_command(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, {}, {})
        assert os.path.basename(cmd[0]) == "pi"
        assert cmd[1:5] == ["--mode", "json", "-p", f"@{self.prompt_file}"]
        assert "--no-session" in cmd
        # Defaults applied
        assert "--provider" in cmd
        assert "--model" in cmd

    def test_full_config_golden(self) -> None:
        """Golden test: Pi with all relevant config keys."""
        config: dict[str, object] = {
            "provider": "openai",
            "model": "gpt-5.5",
            "thinking": "high",
            "tools": ["Read", "Write"],
            "append_system_prompt": "Focus on {{TICKER}}",
            "verbose": True,
        }
        cmd = self.adapter.build_command(self.prompt_file, config, {"TICKER": "AAPL"})
        assert os.path.basename(cmd[0]) == "pi"
        assert cmd[1:] == [
            "--mode",
            "json",
            "-p",
            f"@{self.prompt_file}",
            "--no-session",
            "--provider",
            "openai",
            "--model",
            "gpt-5.5",
            "--thinking",
            "high",
            "--tools",
            "read,write",
            "--append-system-prompt",
            "Focus on AAPL",
            "--verbose",
        ]

    def test_default_provider_and_model(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, {}, {})
        idx_provider = cmd.index("--provider")
        assert cmd[idx_provider + 1] == "anthropic"
        idx_model = cmd.index("--model")
        assert cmd[idx_model + 1] == "sonnet"

    def test_invalid_provider_falls_back(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, {"provider": "unknown"}, {})
        idx = cmd.index("--provider")
        assert cmd[idx + 1] == "anthropic"

    def test_invalid_model_falls_back(self) -> None:
        cmd = self.adapter.build_command(self.prompt_file, {"model": "nonexistent"}, {})
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"

    def test_template_variable_substitution(self) -> None:
        cmd = self.adapter.build_command(
            self.prompt_file,
            {"append_system_prompt": "Date is {{DATE}}"},
            {"DATE": "2026-03-30"},
        )
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "Date is 2026-03-30"

    def test_prepare_env_sets_skip_version_check(self) -> None:
        env = {"PATH": "/usr/bin"}
        result = self.adapter.prepare_env(env, {})
        assert result["PI_SKIP_VERSION_CHECK"] == "1"

    def test_working_directory_returns_none(self) -> None:
        assert self.adapter.working_directory({}) is None

    def test_prepare_env_injects_direct_provider_key_without_argv(self) -> None:
        result = self.adapter.prepare_env(
            {},
            {"provider": "openai", "api_key": "secret-value"},
        )
        assert result["OPENAI_API_KEY"] == "secret-value"
        cmd = self.adapter.build_command(
            self.prompt_file,
            {"provider": "openai", "api_key": "secret-value"},
            {},
        )
        assert "secret-value" not in cmd

    def test_prepare_env_injects_vertex_token_without_argv(self) -> None:
        result = self.adapter.prepare_env(
            {},
            {"provider": "vertex-maas", "api_key": "short-lived-token"},
        )
        assert result["METAPROC_PI_API_KEY"] == "short-lived-token"

    def test_parse_result_event_returns_none(self) -> None:
        """Phase 1: print mode has no structured events."""
        assert self.adapter.parse_result_event('{"type": "result"}') is None


# ── derive_variant tests ─────────────────────────────────────────


class TestDeriveRunTag:
    def test_claude_default_model(self) -> None:
        """Default model (opus) -> bare short_name."""
        assert derive_variant("claude-code-cli", {"model": "opus"}) == "claude-cli"

    def test_claude_no_model(self) -> None:
        """No model config -> bare short_name."""
        assert derive_variant("claude-code-cli", {}) == "claude-cli"

    def test_claude_non_default_model(self) -> None:
        """Non-default model -> short_name-model."""
        assert derive_variant("claude-code-cli", {"model": "sonnet"}) == "claude-cli-sonnet"

    def test_gemini_no_model(self) -> None:
        """Gemini default (None / CLI default) -> bare short_name."""
        assert derive_variant("gemini-cli", {}) == "gemini-cli"

    def test_gemini_explicit_model(self) -> None:
        """Gemini with explicit model -> short_name-model."""
        assert (
            derive_variant("gemini-cli", {"model": "gemini-3.5-flash"})
            == "gemini-cli-gemini-3.5-flash"
        )

    def test_unknown_adapter_uses_type_as_prefix(self) -> None:
        assert derive_variant("aider-cli", {"model": "gpt4"}) == "aider-cli-gpt4"

    def test_model_with_slash_is_sanitized(self) -> None:
        tag = derive_variant("gemini-cli", {"model": "gemini/3.5-flash"})
        assert "/" not in tag
        assert tag == "gemini-cli-gemini-3.5-flash"

    def test_model_with_spaces_is_sanitized(self) -> None:
        tag = derive_variant("claude-code-cli", {"model": "claude 3 opus"})
        assert " " not in tag
        assert tag == "claude-cli-claude-3-opus"

    def test_model_with_path_traversal(self) -> None:
        """Path traversal chars are mapped -- dots are kept but slash becomes dash."""
        tag = derive_variant("claude-code-cli", {"model": "../escape"})
        assert "/" not in tag
        assert tag == "claude-cli-..-escape"


# ── _sanitize_tag tests ──────────────────────────────────────────


class TestSanitizeTag:
    def test_passthrough_clean_string(self) -> None:
        assert _sanitize_tag("claude-opus") == "claude-opus"

    def test_slashes_become_dashes(self) -> None:
        assert _sanitize_tag("gemini/3.5-flash") == "gemini-3.5-flash"

    def test_spaces_become_dashes(self) -> None:
        assert _sanitize_tag("claude 3 opus") == "claude-3-opus"

    def test_consecutive_special_chars_collapse(self) -> None:
        assert _sanitize_tag("a///b") == "a-b"

    def test_leading_trailing_dashes_stripped(self) -> None:
        assert _sanitize_tag("/foo/") == "foo"

    def test_dots_and_underscores_preserved(self) -> None:
        assert _sanitize_tag("model_v2.1") == "model_v2.1"


class TestGeminiToolsBoundaryValidation:
    """An unusable `tools` value must fail loudly, not deny everything."""

    def test_non_collection_tools_raises_instead_of_denying_all(self) -> None:
        with pytest.raises(ValueError, match="must be a list of tool names"):
            _build_gemini_flags({"tools": "Read,Write"}, {})

    def test_empty_tools_list_still_denies_all_deliberately(self) -> None:
        # An explicit empty list is a real boundary ("no tools"), unlike a
        # malformed value, so it keeps installing the deny-all policy.
        flags = _build_gemini_flags({"tools": []}, {})
        assert "--policy" in flags

    def test_absent_tools_key_installs_no_policy(self) -> None:
        assert "--policy" not in _build_gemini_flags({}, {})
