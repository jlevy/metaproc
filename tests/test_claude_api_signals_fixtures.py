"""Regression fixtures for parse_claude_api_signals.

The parser extracts HTTP-axis signals from claude-code-debug.log + stream-json
output. Both formats are emitted by the Claude Code CLI and are subject
to format drift across CLI versions. These fixtures pin the shapes the
parser is known to handle. When Claude Code changes its emitted format,
these tests are the early warning — update the fixtures alongside the
parser, never silently degrade.

Pattern: each fixture file contains realistic content (lifted from real
runs we've observed); each test asserts the structured fields the
parser must extract from it.
"""

from __future__ import annotations

from pathlib import Path

from metaproc.adapters.claude_cli import parse_claude_api_signals

FIXTURES = Path(__file__).parent / "fixtures" / "claude_api_signals"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestApi401AuthenticationError:
    """Stream-json result event with is_error=true + api_error_status=401."""

    def test_extracts_api_status(self):
        signals = parse_claude_api_signals(stdout=_load("api_401_authentication_error.jsonl"))
        assert signals.api_status == 401

    def test_extracts_request_id(self):
        signals = parse_claude_api_signals(stdout=_load("api_401_authentication_error.jsonl"))
        assert signals.request_id == "req_011CaUisUX1JCPbdQaCDRYZ5"

    def test_extracts_error_excerpt(self):
        signals = parse_claude_api_signals(stdout=_load("api_401_authentication_error.jsonl"))
        assert "API Error: 401" in signals.error_body_excerpt
        assert "authentication_error" in signals.error_body_excerpt


class TestApi429RateLimit:
    """Stream-json result event with is_error=true + api_error_status=429 + retry_after."""

    def test_extracts_api_status(self):
        signals = parse_claude_api_signals(stdout=_load("api_429_rate_limit.jsonl"))
        assert signals.api_status == 429

    def test_extracts_retry_after(self):
        signals = parse_claude_api_signals(stdout=_load("api_429_rate_limit.jsonl"))
        assert signals.retry_after_s == 42

    def test_extracts_request_id(self):
        signals = parse_claude_api_signals(stdout=_load("api_429_rate_limit.jsonl"))
        assert signals.request_id == "req_011XYZ429ratelimit"


class TestApi200Success:
    """Successful run emits no error signals — every parser field stays empty."""

    def test_no_signals(self):
        signals = parse_claude_api_signals(stdout=_load("api_200_success.jsonl"))
        assert signals.api_status is None
        assert signals.oauth_refresh_status is None
        assert signals.request_id == ""
        assert signals.error_body_excerpt == ""
        assert signals.retry_after_s is None


class TestKeywordInToolOutputDoesNotMatch:
    """Regression for the false-positive bug: agent-emitted text mentioning
    'authentication_error' / 'invalid_grant' / AxiosError as ordinary
    content (tool result, model thoughts, fetched URLs) must NOT produce
    auth-failure signals. The parser only reports structured signals.
    """

    def test_no_oauth_refresh_signal_from_tool_output(self):
        signals = parse_claude_api_signals(
            stdout=_load("keyword_in_tool_output_no_auth_failure.jsonl")
        )
        # No AxiosError on /oauth/token — keyword in tool result text is
        # NOT a refresh failure.
        assert signals.oauth_refresh_status is None

    def test_no_api_status_when_only_tool_403(self):
        signals = parse_claude_api_signals(
            stdout=_load("keyword_in_tool_output_no_auth_failure.jsonl")
        )
        # The 403 in the tool result body is from www.sec.gov, not the
        # Anthropic API; the parser must not extract api_status from it.
        assert signals.api_status is None


class TestOauthRefresh400DebugLog:
    """claude-code-debug.log AxiosError on /v1/oauth/token + downstream 401."""

    def test_extracts_oauth_refresh_status(self):
        signals = parse_claude_api_signals(debug_text=_load("oauth_refresh_400.log"))
        assert signals.oauth_refresh_status == 400

    def test_extracts_api_status_from_attempt_line(self):
        signals = parse_claude_api_signals(debug_text=_load("oauth_refresh_400.log"))
        assert signals.api_status == 401

    def test_extracts_request_id_from_attempt_body(self):
        signals = parse_claude_api_signals(debug_text=_load("oauth_refresh_400.log"))
        assert signals.request_id == "req_011CaUniwgrZotHqXq9wE63G"


class TestCombinedSources:
    """Realistic dispatch: stream-json result event + slot debug log."""

    def test_combined_extracts_consistent_signals(self):
        # Worker dispatch presents both: session log JSONL has the result
        # event; slot debug log has the OAuth refresh failure that
        # actually caused the 401. Parser merges signals from both.
        signals = parse_claude_api_signals(
            debug_text=_load("oauth_refresh_400.log"),
            stdout=_load("api_401_authentication_error.jsonl"),
        )
        assert signals.api_status == 401
        assert signals.oauth_refresh_status == 400
        # request_id pulled from stdout (preferred source for the body
        # the user sees).
        assert signals.request_id == "req_011CaUisUX1JCPbdQaCDRYZ5"
