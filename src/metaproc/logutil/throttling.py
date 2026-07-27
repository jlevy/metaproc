"""Pair `rate_limit` log events with subsequent quiet gaps to attribute backoff time.

The distinction between policy-related throttling wait, technical
network/API wait, and tool execution time is important: burying
``429`` backoff inside a generic ``api_wait_s`` bucket misleads
operators about whether the bottleneck is provider rate-limit policy
or network latency. This module is the pure data-side helper that
turns adapter `rate_limit` events into typed throttling spans on the
``waiting > throttling > rate_limits > api`` (and budget / quota)
taxonomy paths.

Pairing strategy:

- Each `rate_limit` event with ``is_error=True`` (status="blocked")
  opens a throttling window. The window closes at the timestamp of the
  next event the agent emits in the same session.
- Status="allowed_warning" or other non-blocked rate-limit notices are
  recorded as zero-duration markers so the rollup can show frequency
  even when no backoff happened.
- Sessions that crash mid-throttle leave a final ``ThrottleSpan`` with
  ``ended_at=None`` and ``duration_s=None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from metaproc.logutil.parsing import LogEvent

THROTTLING_RATE_LIMIT_API = ("waiting", "throttling", "rate_limits", "api")
THROTTLING_BUDGET = ("waiting", "throttling", "budget")
THROTTLING_QUOTA = ("waiting", "throttling", "quota")


@dataclass(frozen=True)
class ThrottleSpan:
    """One paired throttling window.

    ``time_kind_path`` is the canonical taxonomy path the joiner accrues
    against ``Metrics.wait_throttling_s`` /
    ``Metrics.wait_rate_limit_s`` /
    ``Metrics.wait_budget_s`` so the same span maps to the right
    canonical metric without per-call branching in the joiner.
    """

    provider: str | None
    limit_type: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_s: float | None
    time_kind_path: tuple[str, ...]
    is_blocked: bool
    raw: dict[str, Any] | None = None


def attribute_throttling(events: list[LogEvent]) -> list[ThrottleSpan]:
    """Walk ``events`` (in arrival order) and emit paired throttling spans.

    Pairing rule:

    1. A `rate_limit` event with ``is_error=True`` opens a window.
    2. The window closes at the timestamp of the next event from the
       same session, regardless of kind. The assumption is that the
       agent could not progress while the rate limit was active, so the
       gap until the next emission *is* the backoff time.
    3. A `rate_limit` event with ``is_error=False`` (allowed_warning,
       reconnect-style notices, etc.) becomes a zero-duration marker
       so the rollup can show frequency even when no backoff happened.
    """
    spans: list[ThrottleSpan] = []
    open_window: _OpenWindow | None = None

    for ev in events:
        if open_window is not None and ev is not open_window.opener:
            spans.append(_close_window(open_window, ended_at=_parse_ts(ev.timestamp)))
            open_window = None

        if ev.kind != "rate_limit":
            continue

        if not ev.is_error:
            spans.append(_marker_span(ev))
            continue

        open_window = _OpenWindow(opener=ev)

    if open_window is not None:
        # Session crashed while the limit was active; surface the half-open span.
        spans.append(_close_window(open_window, ended_at=None))

    return spans


# ── Internals ──────────────────────────────────────────────────────


@dataclass
class _OpenWindow:
    opener: LogEvent
    __hash__ = object.__hash__  # type: ignore[assignment]


def _close_window(window: _OpenWindow, *, ended_at: datetime | None) -> ThrottleSpan:
    started_at = _parse_ts(window.opener.timestamp)
    duration_s = _duration(started_at, ended_at)
    raw = window.opener.raw if isinstance(window.opener.raw, dict) else None
    limit_type = _limit_type(window.opener)
    return ThrottleSpan(
        provider=window.opener.provider,
        limit_type=limit_type,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        time_kind_path=_taxonomy_for(limit_type),
        is_blocked=True,
        raw=raw,
    )


def _marker_span(ev: LogEvent) -> ThrottleSpan:
    """Zero-duration record so the operator can see allowed-warning frequency."""
    started_at = _parse_ts(ev.timestamp)
    raw = ev.raw if isinstance(ev.raw, dict) else None
    limit_type = _limit_type(ev)
    return ThrottleSpan(
        provider=ev.provider,
        limit_type=limit_type,
        started_at=started_at,
        ended_at=started_at,
        duration_s=0.0 if started_at is not None else None,
        time_kind_path=_taxonomy_for(limit_type),
        is_blocked=False,
        raw=raw,
    )


def _limit_type(ev: LogEvent) -> str | None:
    raw = ev.raw if isinstance(ev.raw, dict) else {}
    info = raw.get("rate_limit_info")
    if isinstance(info, dict):
        value = info.get("rateLimitType")
        if isinstance(value, str) and value:
            return value
    return None


def _taxonomy_for(limit_type: str | None) -> tuple[str, ...]:
    if limit_type is None:
        return THROTTLING_RATE_LIMIT_API
    lower = limit_type.lower()
    if "budget" in lower:
        return THROTTLING_BUDGET
    if "quota" in lower:
        return THROTTLING_QUOTA
    return THROTTLING_RATE_LIMIT_API


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _duration(started_at: datetime | None, ended_at: datetime | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    delta = (ended_at - started_at).total_seconds()
    if delta < 0:
        return None
    return delta
