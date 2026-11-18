"""Claude Code harness contract at the pinned CLI release.

These tests pin what the Claude Code 2.1.325 upgrade changed in the adapter
(pin, debug-capture argv, credential scrub) and replay real 2.1.325 captures
through the parsers metaproc runs on Claude output. The captures are in
``tests/fixtures/claude_api_signals/`` and are sanitized: paths are rewritten and
the ``system/init`` event keeps only the fields metaproc and these tests read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.adapters.claude_cli import (
    CLAUDE_CODE_INSTALL_HINT,
    PINNED_CLAUDE_CODE_CLI_VERSION,
    ClaudeCodeCliAdapter,
    parse_claude_api_signals,
)
from metaproc.dispatch.credential_pool import Vehicle
from metaproc.logutil.usage import sum_claude_usage

FIXTURES = Path(__file__).parent / "fixtures" / "claude_api_signals"
STREAM_FIXTURE = FIXTURES / "stream_json_2_1_325_bash_tool_call.jsonl"
DEBUG_404_FIXTURE = FIXTURES / "api_404_model_not_found_2_1_325_debug.txt"

# Profile and Workload Identity Federation sources rank above stored /login
# credentials in Claude Code's documented precedence.
PROFILE_AND_FEDERATION_VARS = (
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
    "ANTHROPIC_SERVICE_ACCOUNT_ID",
    "ANTHROPIC_WORKSPACE_ID",
)


@pytest.fixture
def adapter() -> ClaudeCodeCliAdapter:
    return ClaudeCodeCliAdapter()


def _events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


class TestPinnedRelease:
    def test_pin_is_the_reviewed_release(self) -> None:
        # Claude Fable 5.2 needs Claude Code >= 2.1.321. Moving this pin needs
        # a new supply-chain review in SUPPLY-CHAIN-SECURITY.md.
        assert PINNED_CLAUDE_CODE_CLI_VERSION == "2.1.325"

    def test_install_hint_names_the_pin(self) -> None:
        assert CLAUDE_CODE_INSTALL_HINT.endswith(
            f"@anthropic-ai/claude-code@{PINNED_CLAUDE_CODE_CLI_VERSION}"
        )

    def test_fixture_was_captured_on_the_pin(self) -> None:
        init = next(e for e in _events(STREAM_FIXTURE) if e.get("subtype") == "init")
        assert init["claude_code_version"] == PINNED_CLAUDE_CODE_CLI_VERSION


class TestDebugCaptureArgs:
    def test_debug_file_only(self, adapter: ClaudeCodeCliAdapter, tmp_path: Path) -> None:
        # `--debug-file` alone logs unfiltered. `--debug=api` would bind a
        # filter that drops the `[ERROR] API error` and `[API:auth]` lines the
        # failure classifier reads.
        assert adapter.debug_capture_args(tmp_path) == [
            "--debug-file",
            str(tmp_path / "claude-code-debug.log"),
        ]

    def test_no_category_filter_or_bare_value(
        self, adapter: ClaudeCodeCliAdapter, tmp_path: Path
    ) -> None:
        args = adapter.debug_capture_args(tmp_path)
        assert "-d" not in args
        assert "api" not in args
        assert not any(arg.startswith("--debug=") for arg in args)


class TestCredentialScrub:
    @pytest.mark.parametrize("vehicle", [None, Vehicle.LOGIN_CREDENTIALS, Vehicle.OAUTH_TOKEN])
    def test_scrubs_profile_and_federation_sources(
        self, adapter: ClaudeCodeCliAdapter, vehicle: Vehicle | None
    ) -> None:
        scrub = adapter.credential_scrub_env(vehicle=vehicle)
        for name in PROFILE_AND_FEDERATION_VARS:
            assert scrub[name] == "", name

    @pytest.mark.parametrize("vehicle", [None, Vehicle.LOGIN_CREDENTIALS, Vehicle.OAUTH_TOKEN])
    def test_keeps_api_key_and_profile_directory(
        self, adapter: ClaudeCodeCliAdapter, vehicle: Vehicle | None
    ) -> None:
        scrub = adapter.credential_scrub_env(vehicle=vehicle)
        # API-key mode is refused at acquisition, not silently scrubbed.
        assert "ANTHROPIC_API_KEY" not in scrub
        # Unsetting the directory would only move profile lookup to
        # ~/.config/anthropic; it is not a credential.
        assert "ANTHROPIC_CONFIG_DIR" not in scrub

    def test_vehicle_a_keeps_its_token(self, adapter: ClaudeCodeCliAdapter) -> None:
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in adapter.credential_scrub_env(
            vehicle=Vehicle.OAUTH_TOKEN
        )
        assert adapter.credential_scrub_env(vehicle=None)["CLAUDE_CODE_OAUTH_TOKEN"] == ""


class TestNewModelCommands:
    @pytest.mark.parametrize(
        "model",
        ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    )
    def test_current_ids_pass_through(
        self,
        adapter: ClaudeCodeCliAdapter,
        model: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("METAPROC_SKIP_CLAUDE_VERSION_CHECK", "1")
        config: dict[str, object] = {"model": model, "permission_mode": "bypassPermissions"}
        assert adapter.validate_config(config) == []
        cmd = adapter.build_command(tmp_path / "prompt.md", config, {})
        assert cmd[cmd.index("--model") + 1] == model


class TestStreamJsonCapture:
    """A real `claude -p --output-format stream-json --verbose` run on 2.1.325.

    claude-haiku-5-5, one Bash tool call (`echo probe-ok`), argv built by the
    adapter with `permission_mode: bypassPermissions`.
    """

    def test_result_event_parses(self, adapter: ClaudeCodeCliAdapter) -> None:
        results = [
            parsed
            for line in STREAM_FIXTURE.read_text(encoding="utf-8").splitlines()
            if (parsed := adapter.parse_result_event(line)) is not None
        ]
        assert len(results) == 1
        result = results[0]
        assert result["subtype"] == "success"
        assert result["is_error"] is False
        assert result["num_turns"] == 2
        assert result["result"] == "probe-ok"

    def test_tool_call_round_trip(self) -> None:
        events = _events(STREAM_FIXTURE)
        tool_uses = [
            block
            for e in events
            if e["type"] == "assistant"
            for block in e["message"]["content"]
            if block.get("type") == "tool_use"
        ]
        assert [(t["name"], t["input"]["command"]) for t in tool_uses] == [
            ("Bash", "echo probe-ok")
        ]
        tool_results = [
            block
            for e in events
            if e["type"] == "user"
            for block in e["message"]["content"]
            if block.get("type") == "tool_result"
        ]
        assert [r["content"] for r in tool_results] == ["probe-ok"]

    def test_usage_normalizes_from_model_usage(self) -> None:
        result = next(e for e in _events(STREAM_FIXTURE) if e["type"] == "result")
        stats = sum_claude_usage(result, fallback_model="haiku")
        assert stats.model == "claude-haiku-5-5"
        assert stats.provider == "anthropic"
        assert stats.has_token_usage
        assert stats.has_cost
        model_usage = result["modelUsage"]["claude-haiku-5-5"]
        assert stats.input_tokens == model_usage["inputTokens"]
        assert stats.output_tokens == model_usage["outputTokens"]
        assert stats.cache_read_tokens == model_usage["cacheReadInputTokens"]
        assert stats.cache_write_tokens == model_usage["cacheCreationInputTokens"]
        assert stats.cost_usd == pytest.approx(result["total_cost_usd"])

    def test_success_yields_no_api_error_signals(self) -> None:
        signals = parse_claude_api_signals(stdout=STREAM_FIXTURE.read_text(encoding="utf-8"))
        assert signals.api_status is None
        assert signals.oauth_refresh_status is None
        assert signals.error_body_excerpt == ""
        assert signals.retry_after_s is None

    def test_success_request_id_comes_from_first_assistant_event(self) -> None:
        # 2.1.325 puts a top-level `request_id` on every assistant event, so the
        # stdout fallback now reports the first successful request's ID. The
        # hand-written `api_200_success.jsonl` fixture predates that field.
        signals = parse_claude_api_signals(stdout=STREAM_FIXTURE.read_text(encoding="utf-8"))
        first_assistant = next(e for e in _events(STREAM_FIXTURE) if e["type"] == "assistant")
        assert signals.request_id == first_assistant["request_id"]

    def test_allowed_rate_limit_event_does_not_cool_the_label(
        self, adapter: ClaudeCodeCliAdapter
    ) -> None:
        # 2.1.325 emits an `allowed_warning` rate_limit_event on a healthy run;
        # only `blocked` may mark the credential cooling.
        classification = adapter.classify_failure(None, "", STREAM_FIXTURE)
        assert classification.status == "unknown"


class TestDebugLogCapture:
    """A real 2.1.325 `--debug-file` log of an API 404 (unknown model)."""

    def test_extracts_status_and_request_id(self) -> None:
        signals = parse_claude_api_signals(debug_text=DEBUG_404_FIXTURE.read_text(encoding="utf-8"))
        assert signals.api_status == 404
        assert signals.request_id == "req_011Ch9cSZ7uhYbJAAjhrX1Cf"
        assert signals.oauth_refresh_status is None
        assert signals.error_body_excerpt.startswith("2026-11-18T02:33:51.322Z [ERROR] API error")
