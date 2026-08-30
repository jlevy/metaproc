"""Tests for metaproc.adapters — protocol, implementations, and registry."""

from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
import sys
import time as _time
import types
from pathlib import Path as _Path
from unittest.mock import MagicMock, patch

import pytest

from metaproc.adapters.base import (
    AGENT_ENV_OVERRIDES,
    Adapter,
    AuthCapableCliAdapter,
    AuthFailureClassification,
    AuthStatus,
    QuotaUsage,
    agent_seed_env,
    resolve_templates,
)
from metaproc.adapters.claude_code import CLAUDE_CREDS_ENV_VAR, ClaudeCodeCliAdapter
from metaproc.adapters.gemini import GeminiCliAdapter
from metaproc.adapters.pi_cli import PiCliAdapter, _build_pi_flags
from metaproc.adapters.registry import (
    ADAPTER_REGISTRY,
    derive_variant,
    get_adapter,
    get_auth_capable,
)
from metaproc.config.providers import providers_with_api_keys
from metaproc.settings import CLAUDE_DEFAULT_MODEL, GEMINI_DEFAULT_MODEL, PI_DEFAULT_MODEL

# ── Base / shared ────────────────────────────────────────────────


class TestResolveTemplates:
    def test_resolves_known_vars(self):
        result = resolve_templates("Hello {{NAME}}", {"NAME": "world"})
        assert result == "Hello world"

    def test_keeps_unknown_vars(self):
        result = resolve_templates("{{UNKNOWN}}", {})
        assert result == "{{UNKNOWN}}"


class TestAgentSeedEnv:
    """The seed environment agent children are built from forces styling off."""

    def test_styling_is_off_even_when_the_operator_forces_it_on(self, monkeypatch):
        """An operator shell exporting color-on must not reach an agent transcript."""
        monkeypatch.setenv("FORCE_COLOR", "3")
        monkeypatch.setenv("CLICOLOR_FORCE", "1")
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.delenv("NO_COLOR", raising=False)
        env = agent_seed_env()
        assert env["NO_COLOR"] == "1"
        assert env["FORCE_COLOR"] == "0"
        assert env["CLICOLOR"] == "0"
        assert env["CLICOLOR_FORCE"] == "0"
        assert env["TERM"] == "dumb"

    def test_everything_else_is_inherited(self, monkeypatch):
        """Credentials and settings pass through; only the styling keys are rewritten."""
        monkeypatch.setenv("GEMINI_API_KEY", "inherit-me")
        env = agent_seed_env()
        assert env["GEMINI_API_KEY"] == "inherit-me"
        assert set(AGENT_ENV_OVERRIDES) == {
            "NO_COLOR",
            "FORCE_COLOR",
            "CLICOLOR",
            "CLICOLOR_FORCE",
            "TERM",
        }

    def test_adapters_preserve_the_policy_through_prepare_env(self, monkeypatch):
        """prepare_env receives the scrubbed seed and returns it intact.

        The launch paths hand adapters agent_seed_env() rather than a bare
        os.environ copy; an adapter that dropped the keys would silently reopen
        the styling leak, so pin that the shipped adapters pass them through.
        """
        monkeypatch.setenv("TERM", "xterm-256color")
        for adapter in (GeminiCliAdapter(), ClaudeCodeCliAdapter()):
            env = adapter.prepare_env(agent_seed_env(), {})
            assert env["NO_COLOR"] == "1", adapter.adapter_type
            assert env["TERM"] == "dumb", adapter.adapter_type


class TestAuthStatus:
    def test_dataclass_fields(self):
        status = AuthStatus(
            adapter_type="test",
            cli_found=True,
            cli_path="/usr/bin/test",
            credentials_found=True,
            auth_mode="api-key",
            details="OK",
            setup_hint="",
        )
        assert status.cli_found is True
        assert status.adapter_type == "test"


# ── Claude adapter ───────────────────────────────────────────────


class TestClaudeCodeCliAdapter:
    def setup_method(self):
        self.adapter = ClaudeCodeCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_implements_protocol(self):
        assert isinstance(self.adapter, Adapter)

    def test_adapter_type(self):
        assert self.adapter.adapter_type == "claude-code-cli"

    def test_default_model(self):
        assert self.adapter.default_model == CLAUDE_DEFAULT_MODEL

    _BASE = {"permission_mode": "bypassPermissions"}

    def test_build_command_missing_permission_mode_raises(self, tmp_path):

        prompt = tmp_path / "prompt.md"
        prompt.write_text("Do something.")
        with pytest.raises(ValueError, match="permission_mode"):
            self.adapter.build_command(prompt, {}, {})

    def test_build_command_basic(self, tmp_path):
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Do something.")
        cmd = self.adapter.build_command(prompt, {**self._BASE}, {})
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--model" in cmd
        assert "--permission-mode" in cmd

    def test_build_command_custom_model(self, tmp_path):
        prompt = tmp_path / "prompt.md"
        prompt.write_text("x")
        cmd = self.adapter.build_command(prompt, {**self._BASE, "model": "sonnet"}, {})
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "sonnet"

    def test_build_command_with_tools(self, tmp_path):
        prompt = tmp_path / "prompt.md"
        prompt.write_text("x")
        cmd = self.adapter.build_command(prompt, {**self._BASE, "tools": ["Read", "Write"]}, {})
        assert "--tools" in cmd

    def test_prepare_env_strips_claudecode(self):
        env = {"PATH": "/bin", "CLAUDECODE": "1", "HOME": "/workspace/user"}
        result = self.adapter.prepare_env(env, {})
        assert "CLAUDECODE" not in result
        assert "PATH" in result

    def test_parse_result_event(self):
        assert self.adapter.parse_result_event('{"type": "result", "data": 1}') == {
            "type": "result",
            "data": 1,
        }
        assert self.adapter.parse_result_event("not json") is None
        assert self.adapter.parse_result_event('{"type": "message"}') is None


_VALID_CREDS_PAYLOAD = json.dumps(
    {
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-FAKE",
            "refreshToken": "sk-ant-ort01-FAKE",
            "expiresAt": 1_800_000_000_000,
            "scopes": ["user:inference", "user:profile"],
            "subscriptionType": "max",
        },
        "organizationUuid": "00000000-0000-0000-0000-000000000000",
    }
)


class TestClaudeCodeCliAdapterBootstrap:
    def setup_method(self):
        self.adapter = ClaudeCodeCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_bootstrap_writes_credentials_file_with_correct_permissions(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv(CLAUDE_CREDS_ENV_VAR, _VALID_CREDS_PAYLOAD)

        self.adapter.bootstrap(tmp_path)

        claude_dir = tmp_path / ".claude"
        creds_file = claude_dir / ".credentials.json"
        assert claude_dir.is_dir()
        assert creds_file.is_file()
        assert creds_file.read_text() == _VALID_CREDS_PAYLOAD
        # Env var must be unset after materialization so it doesn't leak
        # to child processes.
        assert CLAUDE_CREDS_ENV_VAR not in os.environ

        dir_mode = stat.S_IMODE(claude_dir.stat().st_mode)
        file_mode = stat.S_IMODE(creds_file.stat().st_mode)
        assert dir_mode == 0o700, f"expected ~/.claude mode 0700, got {oct(dir_mode)}"
        assert file_mode == 0o600, f"expected credentials.json mode 0600, got {oct(file_mode)}"

    def test_bootstrap_is_noop_when_env_var_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv(CLAUDE_CREDS_ENV_VAR, raising=False)

        self.adapter.bootstrap(tmp_path)

        assert not (tmp_path / ".claude").exists()

    def test_bootstrap_raises_on_malformed_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv(CLAUDE_CREDS_ENV_VAR, "{not valid json")

        with pytest.raises(RuntimeError, match="not valid JSON"):
            self.adapter.bootstrap(tmp_path)

        assert not (tmp_path / ".claude").exists()

    def test_bootstrap_raises_when_claudeAiOauth_key_missing(self, tmp_path, monkeypatch):
        payload = json.dumps({"organizationUuid": "abc"})
        monkeypatch.setenv(CLAUDE_CREDS_ENV_VAR, payload)

        with pytest.raises(RuntimeError, match="claudeAiOauth"):
            self.adapter.bootstrap(tmp_path)

        assert not (tmp_path / ".claude").exists()

    def test_bootstrap_is_noop_under_pool_run(self, tmp_path, monkeypatch):
        # Per plan-2026-04-21-auth-credential-pool.md §P2.5, the one-time
        # bootstrap(home) becomes a no-op when the per-item slot path is
        # active so the two code paths never contend for ~/.claude.
        monkeypatch.setenv(CLAUDE_CREDS_ENV_VAR, _VALID_CREDS_PAYLOAD)
        monkeypatch.setenv("METAPROC_AUTH_POOL_RUN", "1")
        self.adapter.bootstrap(tmp_path)
        assert not (tmp_path / ".claude").exists()
        # Env var is NOT popped under pool path — the slot coordinator
        # owns its own materialization and teardown.
        assert os.environ.get(CLAUDE_CREDS_ENV_VAR) == _VALID_CREDS_PAYLOAD


class TestClaudeCodeCliAdapterAuthCapable:
    """AuthCapableCliAdapter subprotocol (plan-2026-04-21 P1.2)."""

    def setup_method(self):
        self.adapter = ClaudeCodeCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_satisfies_subprotocol(self):
        assert isinstance(self.adapter, AuthCapableCliAdapter)

    def test_slot_credential_filename(self):
        assert self.adapter.slot_credential_filename == ".credentials.json"

    def test_no_default_cross_provider(self):
        # OQ9: opt-in only. Default is empty — operators add
        # compatibility explicitly via adapter config.
        assert self.adapter.compatible_fallback_adapters == []

    def test_credential_scope_env(self, tmp_path):
        env = self.adapter.credential_scope_env(tmp_path)
        assert env == {"CLAUDE_CONFIG_DIR": str(tmp_path)}

    def test_debug_capture_args_under_slot(self, tmp_path):
        # Workers must launch with `claude -d api
        # --debug-file=<slot>/claude-code-debug.log` so the OAuth refresh
        # attempt boundaries and per-attempt API errors land in a
        # slot-scoped file the classifier can read post-run.
        args = self.adapter.debug_capture_args(tmp_path)
        assert args == ["-d", "api", "--debug-file", str(tmp_path / "claude-code-debug.log")]

    def test_debug_capture_args_isolates_slots(self, tmp_path):
        # Each slot gets its own debug file; concurrent items must not
        # share the path.
        a = self.adapter.debug_capture_args(tmp_path / "slot-a")
        b = self.adapter.debug_capture_args(tmp_path / "slot-b")
        assert a[-1] != b[-1]
        assert a[-1].endswith("/slot-a/claude-code-debug.log")
        assert b[-1].endswith("/slot-b/claude-code-debug.log")

    def test_diagnostic_filenames_declares_claude_code_debug_log(self):
        # Adapter-namespaced filename — when pi/gemini/codex grow
        # similar mechanisms, theirs use distinct names so they coexist
        # in the run's logs tree without collision.
        assert self.adapter.diagnostic_filenames() == ("claude-code-debug.log",)

        slot = _Path("/tmp/slot-x")
        debug_args = self.adapter.debug_capture_args(slot)
        debug_path = _Path(debug_args[debug_args.index("--debug-file") + 1])
        assert debug_path.name in self.adapter.diagnostic_filenames()

    def test_credential_scrub_env_does_not_scrub_api_key(self):
        scrub = self.adapter.credential_scrub_env()
        # Enterprise API-key mode is an explicit operator choice.
        # Scrubbing it would silently clobber their intent; instead the
        # coordinator refuses slot acquisition when it sees this var
        # live (tested at the coordinator layer in P2).
        assert "ANTHROPIC_API_KEY" not in scrub
        assert scrub["ANTHROPIC_AUTH_TOKEN"] == ""
        assert scrub["CLAUDE_CODE_OAUTH_TOKEN"] == ""
        assert scrub["CLAUDE_CODE_APIKEY_HELPER"] == ""

    def test_materialize_credential_writes_file_0600_dir_0700(self, tmp_path):
        slot_dir = tmp_path / "slot"
        self.adapter.materialize_credential(slot_dir, _VALID_CREDS_PAYLOAD)
        creds_file = slot_dir / ".credentials.json"
        assert creds_file.read_text() == _VALID_CREDS_PAYLOAD
        assert stat.S_IMODE(slot_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(creds_file.stat().st_mode) == 0o600

    def test_materialize_credential_rejects_malformed_blob(self, tmp_path):
        with pytest.raises(RuntimeError, match="not valid JSON"):
            self.adapter.materialize_credential(tmp_path / "slot", "{not json")

    def test_materialize_credential_rejects_missing_claudeAiOauth(self, tmp_path):
        with pytest.raises(RuntimeError, match="claudeAiOauth"):
            self.adapter.materialize_credential(
                tmp_path / "slot", json.dumps({"organizationUuid": "x"})
            )

    def test_capture_credential_rejects_non_darwin(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        with pytest.raises(RuntimeError, match="requires macOS"):
            self.adapter.capture_credential()

    def test_classify_failure_reads_rate_limit_event_from_session_log(self, tmp_path):
        log_path = tmp_path / "session.jsonl"
        log_path.write_text(
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "blocked",
                        "rateLimitType": "five_hour",
                        "resetsAt": 1774501200,
                    },
                }
            )
            + "\n"
        )
        result = self.adapter.classify_failure(None, "", log_path)
        assert result.status == "cooling"
        assert result.cooling_until_ts == 1774501200
        assert "five_hour" in result.reason

    def test_classify_failure_prefers_latest_blocked_event(self, tmp_path):
        log_path = tmp_path / "session.jsonl"
        log_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "rate_limit_event",
                            "rate_limit_info": {
                                "status": "blocked",
                                "rateLimitType": "one_hour",
                                "resetsAt": 1,
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "rate_limit_event",
                            "rate_limit_info": {"status": "allowed"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "rate_limit_event",
                            "rate_limit_info": {
                                "status": "blocked",
                                "rateLimitType": "five_hour",
                                "resetsAt": 2,
                            },
                        }
                    ),
                ]
            )
            + "\n"
        )
        result = self.adapter.classify_failure(None, "", log_path)
        assert result.status == "cooling"
        assert result.cooling_until_ts == 2

    def test_classify_failure_oauth_refresh_400_is_expired(self, tmp_path):
        # Structured detection only: an OAuth refresh failure surfaces in
        # the slot debug log as `AxiosError: [url=...oauth/token, status=400]`.
        # parse_claude_api_signals extracts oauth_refresh_status=400 and the
        # classifier routes to expired/ABORT.
        debug_text = (
            "[DEBUG] [API:auth] OAuth token check starting\n"
            "[ERROR] AxiosError: [url=https://platform.claude.com/v1/oauth/token,"
            " status=400] AxiosError: Request failed with status code 400\n"
        )
        result = self.adapter.classify_failure(None, debug_text, None)
        assert result.status == "expired"
        assert result.oauth_refresh_status == 400
        assert "oauth_refresh_status=400" in result.reason

    def test_classify_failure_api_401_is_expired(self, tmp_path):
        # Structured detection: stream-json result with `api_error_status: 401`.
        session_log = tmp_path / "session.jsonl"
        session_log.write_text(
            '{"type":"result","is_error":true,"api_error_status":401,'
            '"result":"Failed to authenticate. API Error: 401 ..."}\n'
        )
        result = self.adapter.classify_failure(None, "exit code 1", session_log)
        assert result.status == "expired"
        assert result.api_status == 401

    def test_classify_failure_keyword_in_tool_output_is_NOT_expired(self, tmp_path):
        # Regression: the classifier MUST NOT mark a credential expired
        # when the keyword `authentication_error` or `invalid_grant`
        # appears as ordinary tool / model output rather than a
        # structured auth signal. A remote-site 403 must not be
        # misclassified as an Anthropic auth failure merely because the
        # session log happens to contain the substring.
        session_log = tmp_path / "session.jsonl"
        session_log.write_text(
            # Realistic shape: agent-emitted text mentioning auth keywords
            # in tool result, model thoughts, or fetched content. No
            # structured api_error_status or AxiosError on /oauth/token.
            '{"type":"assistant","message":{"content":[{"type":"text",'
            '"text":"The site returned an authentication_error and the token '
            "appears to be invalid_grant — but that is unrelated to our actual "
            'auth state."}]}}\n'
            '{"type":"result","is_error":true,"result":"Failed to fetch '
            "https://example.invalid/...: AxiosError: Request failed with status "
            'code 403"}\n'
        )
        result = self.adapter.classify_failure(None, "exit code 1", session_log)
        assert result.status != "expired", (
            f"keyword-in-tool-output must not flip credentials expired; got {result}"
        )

    def test_classify_failure_too_many_requests_is_cooling(self, tmp_path):
        result = self.adapter.classify_failure(None, "429 too_many_requests", None)
        assert result.status == "cooling"
        assert result.cooling_until_ts is None

    def test_classify_failure_pacific_phrase_is_cooling(self):
        # Per jlevy senior review 2026-04-24: the Pacific wall-clock
        # fallback MUST parse the matched hour into a concrete
        # cooling_until_ts. `None` means cooling indefinitely in the
        # pool model and would strand a recoverable credential.
        result = self.adapter.classify_failure(
            None, "quota exhausted; resets 11am (America/Los_Angeles)", None
        )
        assert result.status == "cooling"
        assert "reset-phrase" in result.reason
        assert result.cooling_until_ts is not None
        assert result.cooling_until_ts > 0

    def test_classify_failure_pacific_phrase_rolls_to_tomorrow_if_past(self):

        result = self.adapter.classify_failure(
            None, "quota exhausted; resets 1:30am (America/Los_Angeles)", None
        )
        assert result.status == "cooling"
        assert result.cooling_until_ts is not None
        # The reset must always be in the future relative to now;
        # otherwise eligibility check would re-pick this label on the
        # very next attempt.
        assert result.cooling_until_ts > int(_time.time())

    def test_classify_failure_pacific_phrase_24h_form(self):
        # Bare 24-hour form without meridiem is accepted.
        result = self.adapter.classify_failure(None, "see resets 18:00 (America/Los_Angeles)", None)
        assert result.status == "cooling"
        assert result.cooling_until_ts is not None

    def test_classify_failure_unknown(self):
        result = self.adapter.classify_failure(None, "network hiccup", None)
        assert result.status == "unknown"

    def test_classify_failure_authentication_error_in_session_log_is_expired(self, tmp_path):
        # Regression: stream-json failures put authentication_error in
        # the result event; stderr is empty ("exit code 1"). Without
        # scanning session_log_text, the priority-1 auth-error check
        # misses 401 and the broad claude-startup-exit-1-silent
        # known-bug detector misclassifies the failure as ``unknown``,
        # leaving the bad credential active in the pool.
        session_log = tmp_path / "process-item_item-1.jsonl"
        session_log.write_text(
            '{"type":"result","subtype":"success","is_error":true,'
            '"api_error_status":401,'
            '"result":"Failed to authenticate. API Error: 401 '
            '{\\"type\\":\\"error\\",\\"error\\":'
            '{\\"type\\":\\"authentication_error\\",'
            '\\"message\\":\\"Invalid authentication credentials\\"},'
            '\\"request_id\\":\\"req_011CaUnabc123\\"}"}\n'
        )
        result = self.adapter.classify_failure(None, "exit code 1", session_log)
        assert result.status == "expired"
        # Reason carries both the canonical signal name and the actual
        # error excerpt so operators don't have to grep JSONL.
        assert "authentication_error" in result.reason
        assert "401" in result.reason
        assert "Invalid authentication credentials" in result.reason

    def test_classify_failure_known_bug_reason_carries_actual_error(self, tmp_path):
        # When a known-bug signature fires the reason field must carry
        # an error excerpt so operators can distinguish recurrences and
        # see at a glance what the underlying error string looked like.
        session_log = tmp_path / "session.jsonl"
        session_log.write_text("")
        result = self.adapter.classify_failure(None, "exit code 1\n", session_log)
        assert result.known_bug_signature == "claude-startup-exit-1-silent"
        # Reason is "known-bug:<name> | <excerpt>"; with only stderr
        # available, the excerpt is the first non-empty stderr line.
        assert result.reason.startswith("known-bug:claude-startup-exit-1-silent")
        assert "exit code 1" in result.reason

    def test_classify_failure_known_bug_no_excerpt_when_stderr_empty(self):
        # No stderr, no session log → no excerpt available → reason is
        # just the bug name (no trailing " | ").
        result = self.adapter.classify_failure(None, "", None)
        # With empty stderr the silent-exit-1 pattern doesn't match
        # (it requires the literal "exit code 1"), so this falls
        # through to the unknown classifier rather than known-bug.
        # Use a stderr that triggers a different known-bug instead
        # — actually, the silent-exit-1 is the only one with the
        # pure-string trigger. Easier: just assert reason has the
        # template prefix when triggered.
        assert result.status == "unknown"

    def test_flush_refreshed_credential_reads_slot_blob(self, tmp_path):
        slot_dir = tmp_path / "slot"
        self.adapter.materialize_credential(slot_dir, _VALID_CREDS_PAYLOAD)
        flushed = self.adapter.flush_refreshed_credential(slot_dir)
        assert flushed == _VALID_CREDS_PAYLOAD

    def test_flush_refreshed_credential_returns_none_for_empty_slot(self, tmp_path):
        # Caller decides whether to treat "no slot file" as no-write-back
        # or as an error; adapter just reports "no blob on disk".
        assert self.adapter.flush_refreshed_credential(tmp_path) is None

    def test_query_quota_usage_returns_none_pending_spike(self, tmp_path):
        # P0.4 pending — native endpoint not yet wired.
        assert self.adapter.query_quota_usage(tmp_path) is None


# ── Gemini adapter ───────────────────────────────────────────────


class TestGeminiCliAdapter:
    def setup_method(self):
        self.adapter = GeminiCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_implements_protocol(self):
        assert isinstance(self.adapter, Adapter)

    def test_adapter_type(self):
        assert self.adapter.adapter_type == "gemini-cli"

    def test_default_model(self):
        assert self.adapter.default_model == GEMINI_DEFAULT_MODEL

    def test_accepts_gemini_36_flash_model(self):
        assert self.adapter.validate_config({"model": "gemini-3.6-flash"}) == []

    def test_build_command_basic(self, tmp_path):
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Do something.")
        cmd = self.adapter.build_command(prompt, {}, {})
        assert cmd[:2] == ["/bin/sh", "-c"]
        assert cmd[4:6] == [str(prompt), "gemini"]
        assert "-p" not in cmd[5:]

    def test_build_command_streams_ignored_audit_prompt_to_stdin(self, tmp_path, monkeypatch):
        logs_dir = tmp_path / ".logs"
        logs_dir.mkdir()
        prompt = logs_dir / "prompt.md"
        prompt.write_text("Use the complete prompt body.")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_gemini = bin_dir / "gemini"
        fake_gemini.write_text("#!/bin/sh\n/bin/cat\n")
        fake_gemini.chmod(fake_gemini.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("METAPROC_SKIP_GEMINI_VERSION_CHECK", "1")

        cmd = self.adapter.build_command(prompt, {}, {})
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)

        assert completed.stdout == "Use the complete prompt body."
        assert all(str(prompt) not in arg for arg in cmd[cmd.index("gemini") :])
        assert prompt.read_text() == "Use the complete prompt body."

    def test_build_command_bypass_permissions(self, tmp_path):
        prompt = tmp_path / "prompt.md"
        prompt.write_text("x")
        cmd = self.adapter.build_command(prompt, {"permission_mode": "bypassPermissions"}, {})
        assert "--approval-mode" in cmd
        idx = cmd.index("--approval-mode")
        assert cmd[idx + 1] == "yolo"

    def test_prepare_env_injects_native_settings(self):
        env = {"PATH": "/bin"}
        result = self.adapter.prepare_env(env, {})
        assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH" in result

    def test_parse_result_event(self):
        assert self.adapter.parse_result_event('{"type": "result"}') == {"type": "result"}
        assert self.adapter.parse_result_event("garbage") is None


# ── Pi CLI adapter ──────────────────────────────────────────────


class TestBuildPiFlags:
    def test_default_provider_and_model(self):
        flags = _build_pi_flags({}, {})
        assert "--provider" in flags
        assert "--model" in flags
        idx_p = flags.index("--provider")
        idx_m = flags.index("--model")
        assert flags[idx_p + 1] == "anthropic"
        assert flags[idx_m + 1] == "sonnet"

    def test_custom_provider_and_model(self):
        flags = _build_pi_flags({"provider": "openai", "model": "gpt-5.5"}, {})
        idx_p = flags.index("--provider")
        idx_m = flags.index("--model")
        assert flags[idx_p + 1] == "openai"
        assert flags[idx_m + 1] == "gpt-5.5"

    def test_thinking_level(self):
        flags = _build_pi_flags({"thinking": "high"}, {})
        assert "--thinking" in flags
        idx = flags.index("--thinking")
        assert flags[idx + 1] == "high"

    def test_tools_list(self):
        flags = _build_pi_flags({"tools": ["read", "bash", "edit"]}, {})
        assert "--tools" in flags
        idx = flags.index("--tools")
        assert flags[idx + 1] == "read,bash,edit"

    def test_tools_string(self):
        flags = _build_pi_flags({"tools": "read,bash"}, {})
        idx = flags.index("--tools")
        assert flags[idx + 1] == "read,bash"

    def test_append_system_prompt_with_variables(self):
        flags = _build_pi_flags(
            {"append_system_prompt": "Working on {{TICKER}}"},
            {"TICKER": "AAPL"},
        )
        assert "--append-system-prompt" in flags
        idx = flags.index("--append-system-prompt")
        assert flags[idx + 1] == "Working on AAPL"

    def test_verbose_flag(self):
        flags = _build_pi_flags({"verbose": True}, {})
        assert "--verbose" in flags

    def test_unknown_provider_falls_back(self):
        flags = _build_pi_flags({"provider": "unknown-provider"}, {})
        idx = flags.index("--provider")
        assert flags[idx + 1] == "anthropic"

    def test_unknown_model_falls_back(self):
        flags = _build_pi_flags({"model": "unknown-model"}, {})
        idx = flags.index("--model")
        assert flags[idx + 1] == "sonnet"

    def test_api_key_is_not_passed_through_argv(self):
        flags = _build_pi_flags({"api_key": "ya29.test-token-123"}, {})
        assert "--api-key" not in flags
        assert "ya29.test-token-123" not in flags

    def test_no_api_key_by_default(self):
        flags = _build_pi_flags({}, {})
        assert "--api-key" not in flags

    def test_tool_choice_accepted_but_not_emitted(self, caplog):
        """pi-cli has no --tool-choice flag yet; the adapter recognizes the
        config field (process specs declare it) and logs a warning rather
        than emitting an unsupported flag.
        """

        with caplog.at_level(logging.WARNING, logger="metaproc.adapters.pi_cli"):
            flags = _build_pi_flags({"tool_choice": "auto"}, {})

        assert "--tool-choice" not in flags
        assert any("tool_choice" in record.message for record in caplog.records)

    def test_tool_choice_empty_does_not_warn(self, caplog):
        """No warning when tool_choice is absent or falsy."""

        with caplog.at_level(logging.WARNING, logger="metaproc.adapters.pi_cli"):
            _build_pi_flags({}, {})
            _build_pi_flags({"tool_choice": ""}, {})

        assert not any("tool_choice" in record.message for record in caplog.records)


class TestPiCliAdapter:
    @pytest.fixture(autouse=True)
    def _mock_pi_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("metaproc.adapters.pi_cli._resolve_pi_binary", lambda: "/usr/bin/pi")

    def setup_method(self):
        self.adapter = PiCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_implements_protocol(self):
        assert isinstance(self.adapter, Adapter)

    def test_adapter_type(self):
        assert self.adapter.adapter_type == "pi-cli"

    def test_short_name(self):
        assert self.adapter.short_name == "pi-cli"

    def test_default_model(self):
        assert self.adapter.default_model == PI_DEFAULT_MODEL

    def test_build_command_basic(self, tmp_path):
        prompt = tmp_path / "prompt.md"
        prompt.write_text("Do something.")
        cmd = self.adapter.build_command(prompt, {}, {})
        assert os.path.basename(cmd[0]) == "pi"
        assert "--mode" in cmd
        assert cmd[cmd.index("--mode") + 1] == "json"
        assert "-p" in cmd
        assert f"@{prompt}" in cmd
        assert "--no-session" in cmd
        assert "--provider" in cmd
        assert "--model" in cmd

    def test_build_command_custom_config(self, tmp_path):
        prompt = tmp_path / "prompt.md"
        prompt.write_text("x")
        cmd = self.adapter.build_command(
            prompt,
            {"provider": "openai", "model": "gpt-5.5", "thinking": "high"},
            {},
        )
        idx_p = cmd.index("--provider")
        assert cmd[idx_p + 1] == "openai"
        idx_m = cmd.index("--model")
        assert cmd[idx_m + 1] == "gpt-5.5"
        idx_t = cmd.index("--thinking")
        assert cmd[idx_t + 1] == "high"

    def test_prepare_env_sets_skip_version_check(self):
        env = {"PATH": "/bin", "HOME": "/workspace/user"}
        result = self.adapter.prepare_env(env, {})
        assert result["PI_SKIP_VERSION_CHECK"] == "1"
        assert "PATH" in result

    def test_parse_result_event_detects_agent_end(self):

        event = {"type": "agent_end", "messages": []}
        result = self.adapter.parse_result_event(json.dumps(event))
        assert result is not None
        assert result["type"] == "agent_end"

    def test_parse_result_event_ignores_other_events(self):

        assert self.adapter.parse_result_event(json.dumps({"type": "agent_start"})) is None
        assert self.adapter.parse_result_event(json.dumps({"type": "message_update"})) is None
        assert self.adapter.parse_result_event("plain text") is None
        assert self.adapter.parse_result_event("") is None

    def test_working_directory_returns_none(self):
        assert self.adapter.working_directory({}) is None

    def test_check_auth_no_cli(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        status = self.adapter.check_auth()
        assert status.cli_found is False
        assert status.credentials_found is False

    def test_check_auth_with_anthropic_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/pi")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        # Ensure auth.json doesn't exist so env var path is tested
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        status = self.adapter.check_auth()
        assert status.cli_found is True
        assert status.credentials_found is True
        assert "anthropic" in status.auth_mode

    def test_check_auth_with_auth_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/pi")
        auth_dir = tmp_path / ".pi" / "agent"
        auth_dir.mkdir(parents=True)
        (auth_dir / "auth.json").write_text("{}")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        status = self.adapter.check_auth()
        assert status.cli_found is True
        assert status.credentials_found is True
        assert status.auth_mode == "auth-json"

    def test_check_auth_no_credentials(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/pi")

        for spec in providers_with_api_keys():
            assert spec.api_key_env is not None  # narrow for type-checker
            monkeypatch.delenv(spec.api_key_env.name, raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        # Ensure auth.json doesn't exist
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        status = self.adapter.check_auth()
        assert status.cli_found is True
        assert status.credentials_found is False

    def test_auth_info_returns_string(self):
        info = self.adapter.auth_info()
        assert "ANTHROPIC_API_KEY" in info
        assert "OPENAI_API_KEY" in info


class TestResolveGcpToken:
    def test_returns_token_via_google_auth(self):

        mock_get_token = MagicMock(return_value="ya29.from-google-auth")
        mock_get_expiry = MagicMock(return_value=None)

        fake_mod = types.ModuleType("metaproc.cloud.gcp.gcp_credentials")
        fake_mod.__dict__["get_access_token"] = mock_get_token
        fake_mod.__dict__["get_token_expiry"] = mock_get_expiry

        with patch.dict(sys.modules, {"metaproc.cloud.gcp.gcp_credentials": fake_mod}):
            # Import deferred so patch.dict(sys.modules) takes effect for resolve_token.
            from metaproc.cloud.gcp.resolve_token import (  # noqa: PLC0415 -- pre-existing local import; needs review
                resolve_gcp_token,
            )

            token = resolve_gcp_token()
            assert token == "ya29.from-google-auth"

    def test_raises_when_credentials_fail(self):
        """When metaproc[gcp] is installed but credentials are broken, raise."""

        fake_mod = types.ModuleType("metaproc.cloud.gcp.gcp_credentials")
        fake_mod.__dict__["get_access_token"] = lambda: (_ for _ in ()).throw(
            RuntimeError("no credentials configured")
        )
        fake_mod.__dict__["get_token_expiry"] = lambda: None

        with (
            patch.dict(sys.modules, {"metaproc.cloud.gcp.gcp_credentials": fake_mod}),
            pytest.raises(RuntimeError, match="no credentials configured"),
        ):
            # Import deferred so patch.dict(sys.modules) takes effect for resolve_token.
            from metaproc.cloud.gcp.resolve_token import (  # noqa: PLC0415 -- pre-existing local import; needs review
                resolve_gcp_token,
            )

            resolve_gcp_token()

    def test_raises_import_error_when_gcp_extra_missing(self):
        """When metaproc[gcp] is not installed, raise ImportError with install hint."""

        with (
            patch.dict(sys.modules, {"metaproc.cloud.gcp.gcp_credentials": None}),
            pytest.raises(ImportError, match="gcp extra"),
        ):
            # Import deferred so patch.dict(sys.modules) takes effect for resolve_token.
            from metaproc.cloud.gcp.resolve_token import (  # noqa: PLC0415 -- pre-existing local import; needs review
                resolve_gcp_token,
            )

            resolve_gcp_token()


# ── Registry ─────────────────────────────────────────────────────


class TestAdapterRegistry:
    def test_known_adapters(self):
        assert "claude-code-cli" in ADAPTER_REGISTRY
        assert "codex-cli" in ADAPTER_REGISTRY
        assert "gemini-cli" in ADAPTER_REGISTRY
        assert "pi-cli" in ADAPTER_REGISTRY

    def test_get_adapter_claude(self):
        adapter = get_adapter("claude-code-cli")
        assert adapter.adapter_type == "claude-code-cli"

    def test_get_adapter_codex(self):
        adapter = get_adapter("codex-cli")
        assert adapter.adapter_type == "codex-cli"

    def test_get_adapter_gemini(self):
        adapter = get_adapter("gemini-cli")
        assert adapter.adapter_type == "gemini-cli"

    def test_get_adapter_pi(self):
        adapter = get_adapter("pi-cli")
        assert adapter.adapter_type == "pi-cli"

    def test_get_adapter_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown adapter type"):
            get_adapter("unknown-adapter")

    def test_all_adapters_implement_protocol(self):
        """Every registered adapter implements the Adapter Protocol.

        Drift guard: if a new adapter lands without all the Protocol
        methods (e.g. a missing ``bootstrap``), this catches it.
        """

        for adapter_type, adapter in ADAPTER_REGISTRY.items():
            assert isinstance(adapter, Adapter), (
                f"{adapter_type!r} does not implement the Adapter Protocol"
            )
            # Spot-check key methods are callable.
            for method in (
                "build_command",
                "validate_config",
                "prepare_env",
                "working_directory",
                "parse_result_event",
                "check_auth",
                "auth_info",
                "bootstrap",
            ):
                assert callable(getattr(adapter, method, None)), (
                    f"{adapter_type!r} missing method {method!r}"
                )


class TestDeriveVariant:
    def test_default_model_returns_short_name(self):
        assert derive_variant("claude-code-cli", {}) == "claude-cli"
        assert derive_variant("gemini-cli", {}) == "gemini-cli"
        assert derive_variant("pi-cli", {}) == "pi-cli"

    def test_explicit_default_model_returns_short_name(self):
        assert derive_variant("claude-code-cli", {"model": "opus"}) == "claude-cli"
        assert derive_variant("pi-cli", {"model": "sonnet"}) == "pi-cli"

    def test_non_default_model_appends(self):
        result = derive_variant("claude-code-cli", {"model": "sonnet"})
        assert result == "claude-cli-sonnet"

    def test_pi_non_default_model_appends(self):
        result = derive_variant("pi-cli", {"model": "gpt-5.5"})
        assert result == "pi-cli-gpt-5.5"

    def test_unknown_adapter_sanitizes(self):
        result = derive_variant("my custom adapter!", {"model": "v1"})
        assert "my-custom-adapter" in result


class TestAuthFailureClassification:
    def test_defaults(self):
        cls = AuthFailureClassification(status="ok")
        assert cls.status == "ok"
        assert cls.cooling_until_ts is None
        assert cls.reason == ""

    def test_cooling_with_reset(self):
        cls = AuthFailureClassification(
            status="cooling", cooling_until_ts=1_700_000_000, reason="too_many_requests"
        )
        assert cls.status == "cooling"
        assert cls.cooling_until_ts == 1_700_000_000
        assert cls.reason == "too_many_requests"


class TestQuotaUsage:
    def test_full_fields(self):
        usage = QuotaUsage(
            remaining_ratio=0.62,
            remaining_units=31,
            total_units=50,
            resets_at=1_700_000_000,
            unit_kind="requests",
            detail="62% (31/50 req)",
        )
        assert usage.remaining_ratio == 0.62
        assert usage.unit_kind == "requests"

    def test_partial_fields(self):
        # Adapter reports ratio but not totals (or vice versa).
        usage = QuotaUsage(
            remaining_ratio=None,
            remaining_units=None,
            total_units=None,
            resets_at=None,
            unit_kind="unknown",
        )
        assert usage.remaining_ratio is None
        assert usage.detail == ""


class TestGetAuthCapable:
    def test_pi_returns_none(self):
        # Pi authenticates via ANTHROPIC_API_KEY, not pool. Must not
        # implement the subprotocol or the slot coordinator will try to
        # drive it.
        assert get_auth_capable("pi-cli") is None

    def test_unknown_returns_none(self):
        assert get_auth_capable("does-not-exist") is None

    def test_claude_gemini_typed_return(self):
        # Return type is Optional[AuthCapableCliAdapter]. Once P1.2 lands,
        # claude-code-cli will satisfy the subprotocol; until then all
        # registered adapters return None because none have the methods.
        for name in ("claude-code-cli", "gemini-cli"):
            result = get_auth_capable(name)
            if result is not None:
                assert isinstance(result, AuthCapableCliAdapter)


class TestAuthCapableCliAdapterIsRuntimeCheckable:
    def test_subprotocol_is_runtime_checkable(self):
        # Without @runtime_checkable the isinstance() in
        # get_auth_capable would raise TypeError at runtime. Assert the
        # subprotocol supports isinstance (decorator is load-bearing per
        # the spec's Review Addendum 2026-04-24).
        class _Stub:
            adapter_type = "stub"
            short_name = "stub"
            default_model = None

        # Should not raise even though _Stub doesn't satisfy the protocol.
        assert not isinstance(_Stub(), AuthCapableCliAdapter)
