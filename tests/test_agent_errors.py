"""Unit tests for the Claude agent error text classifier."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from metaproc.agent_errors import (
    QuotaExhaustedError,
    classify_agent_error,
    parse_quota_reset_at,
    parse_quota_reset_clock,
)


def test_classify_quota_exhausted_with_extra():
    text = "You're out of extra usage · resets 3:10am (America/Los_Angeles)"
    assert classify_agent_error(text) == "quota_exhausted"


def test_classify_quota_exhausted_without_extra():
    assert classify_agent_error("You're out of usage") == "quota_exhausted"


def test_classify_quota_case_insensitive():
    assert classify_agent_error("OUT OF EXTRA USAGE") == "quota_exhausted"


def test_classify_auth_required():
    assert classify_agent_error("Authentication required: token expired") == "auth_required"


def test_classify_auth_required_lowercase_authenticate():
    assert classify_agent_error("please authenticate again") == "auth_required"


def test_classify_agent_timeout():
    assert classify_agent_error("Operation timed out after 600s") == "agent_timeout"


def test_classify_timeout_variant():
    assert classify_agent_error("request timeout") == "agent_timeout"


def test_classify_generic_falls_back_to_agent_error():
    assert classify_agent_error("Some random failure") == "agent_error"


def test_classify_none_returns_agent_error():
    assert classify_agent_error(None) == "agent_error"


def test_classify_empty_string_returns_agent_error():
    assert classify_agent_error("") == "agent_error"


def test_parse_quota_reset_clock_with_minutes():
    text = "You're out of extra usage · resets 3:10am (America/Los_Angeles)"
    assert parse_quota_reset_clock(text) == (3, 10, "am")


def test_parse_quota_reset_clock_no_minutes():
    assert parse_quota_reset_clock("resets 5pm") == (5, 0, "pm")


def test_parse_quota_reset_clock_no_clause_returns_none():
    assert parse_quota_reset_clock("You're out of usage") is None


def test_parse_quota_reset_clock_none_input():
    assert parse_quota_reset_clock(None) is None


def test_parse_quota_reset_at_today_when_clock_is_after_now():
    """02:32 PDT now, message says 'resets 3:10am' → today's 03:10 PDT."""
    pdt = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 5, 13, 2, 32, 0, tzinfo=pdt)
    text = "You're out of extra usage · resets 3:10am (America/Los_Angeles)"
    reset = parse_quota_reset_at(text, now=now)
    assert reset is not None
    assert reset == datetime(2026, 5, 13, 3, 10, 0, tzinfo=pdt)


def test_parse_quota_reset_at_tomorrow_when_clock_is_past():
    """05:00 PDT now, message says 'resets 3:10am' → tomorrow's 03:10 PDT."""
    pdt = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 5, 13, 5, 0, 0, tzinfo=pdt)
    text = "out of usage · resets 3:10am"
    reset = parse_quota_reset_at(text, now=now)
    assert reset is not None
    assert reset == datetime(2026, 5, 14, 3, 10, 0, tzinfo=pdt)


def test_parse_quota_reset_at_pm_clock():
    pdt = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=pdt)
    reset = parse_quota_reset_at("out of usage · resets 5:00pm", now=now)
    assert reset == datetime(2026, 5, 13, 17, 0, 0, tzinfo=pdt)


def test_parse_quota_reset_at_returns_none_without_clause():
    assert parse_quota_reset_at("some other error") is None


def test_quota_exhausted_error_carries_reset_at():
    pdt = ZoneInfo("America/Los_Angeles")
    reset = datetime(2026, 5, 13, 3, 10, 0, tzinfo=pdt)
    err = QuotaExhaustedError("out of usage", reset_at=reset)
    assert err.reset_at == reset
    assert "out of usage" in str(err)
