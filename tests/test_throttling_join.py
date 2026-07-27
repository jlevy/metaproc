"""Tests for the throttling/backoff joiner (B11, spec §3.3 + §6.7)."""

from __future__ import annotations

from metaproc.logutil.parsing import LogEvent
from metaproc.logutil.throttling import (
    THROTTLING_BUDGET,
    THROTTLING_QUOTA,
    THROTTLING_RATE_LIMIT_API,
    attribute_throttling,
)


def _rl(
    ts: str,
    *,
    blocked: bool = True,
    limit_type: str | None = "rate_limits",
    provider: str = "anthropic",
) -> LogEvent:
    raw = {
        "rate_limit_info": {
            "rateLimitType": limit_type or "unknown",
            "status": "blocked" if blocked else "allowed_warning",
        }
    }
    return LogEvent(
        kind="rate_limit",
        summary=f"[rate_limit:{'blocked' if blocked else 'allowed_warning'}]",
        adapter="claude",
        provider=provider,
        timestamp=ts,
        is_error=blocked,
        raw=raw,
    )


def _other(ts: str, kind: str = "tool_call") -> LogEvent:
    return LogEvent(kind=kind, summary="[other]", adapter="claude", timestamp=ts)


def test_blocked_event_paired_with_next_event_yields_duration() -> None:
    events = [
        _rl("2026-04-24T12:00:00Z", blocked=True),
        _other("2026-04-24T12:00:04.5Z"),
    ]
    spans = attribute_throttling(events)
    assert len(spans) == 1
    span = spans[0]
    assert span.is_blocked
    assert span.duration_s == 4.5
    assert span.time_kind_path == THROTTLING_RATE_LIMIT_API
    assert span.provider == "anthropic"


def test_allowed_warning_yields_zero_duration_marker() -> None:
    events = [_rl("2026-04-24T12:00:00Z", blocked=False)]
    span = attribute_throttling(events)[0]
    assert not span.is_blocked
    assert span.duration_s == 0.0


def test_budget_limit_routes_to_budget_taxonomy() -> None:
    events = [
        _rl("2026-04-24T12:00:00Z", blocked=True, limit_type="MAX_BUDGET_REACHED"),
        _other("2026-04-24T12:00:01Z"),
    ]
    span = attribute_throttling(events)[0]
    assert span.time_kind_path == THROTTLING_BUDGET


def test_quota_limit_routes_to_quota_taxonomy() -> None:
    events = [
        _rl("2026-04-24T12:00:00Z", blocked=True, limit_type="org_quota_exceeded"),
        _other("2026-04-24T12:00:01Z"),
    ]
    span = attribute_throttling(events)[0]
    assert span.time_kind_path == THROTTLING_QUOTA


def test_unknown_limit_type_falls_back_to_rate_limits() -> None:
    events = [
        _rl("2026-04-24T12:00:00Z", blocked=True, limit_type=None),
        _other("2026-04-24T12:00:01Z"),
    ]
    span = attribute_throttling(events)[0]
    assert span.time_kind_path == THROTTLING_RATE_LIMIT_API


def test_session_crash_after_rate_limit_yields_half_open_span() -> None:
    events = [_rl("2026-04-24T12:00:00Z", blocked=True)]
    span = attribute_throttling(events)[0]
    assert span.is_blocked
    assert span.ended_at is None
    assert span.duration_s is None


def test_consecutive_blocks_pair_each_with_next_event() -> None:
    events = [
        _rl("2026-04-24T12:00:00Z", blocked=True),
        _rl("2026-04-24T12:00:01Z", blocked=True),  # this closes #0
        _other("2026-04-24T12:00:02Z"),  # this closes #1
    ]
    spans = attribute_throttling(events)
    assert len(spans) == 2
    assert spans[0].duration_s == 1.0
    assert spans[1].duration_s == 1.0


def test_negative_duration_yields_none_not_negative() -> None:
    """Out-of-order timestamps must not poison the wait_throttling_s rollup."""
    events = [
        _rl("2026-04-24T12:00:05Z", blocked=True),
        _other("2026-04-24T12:00:00Z"),
    ]
    span = attribute_throttling(events)[0]
    assert span.duration_s is None
