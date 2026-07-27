"""Pattern-based classifier for Claude agent error text.

Used by:

- :mod:`metaproc.trace.extractors.claude_agent` to populate span
  ``error.code`` / ``error.message`` from the ``result.result`` text on
  failed attempts.
- :mod:`metaproc.adapters.claude_code` to raise typed exceptions (e.g.
  ``QuotaExhaustedError``) so the runpool can pause-and-resume at the
  pool level instead of crashing every queued item.

Patterns are intentionally narrow: a span/attempt that doesn't match a
known pattern stays classified as ``agent_error`` so it's still surfaced,
just not over-attributed.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

AgentErrorCode = Literal["quota_exhausted", "auth_required", "agent_timeout", "agent_error"]

_QUOTA_PATTERN: Final = re.compile(r"out of (extra )?usage", re.IGNORECASE)
_AUTH_PATTERN: Final = re.compile(r"authenticat", re.IGNORECASE)
_TIMEOUT_PATTERN: Final = re.compile(r"\btime(?:d? out|out)\b", re.IGNORECASE)

_RESET_PATTERN: Final = re.compile(
    r"resets?\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)",
    re.IGNORECASE,
)


def classify_agent_error(text: str | None) -> AgentErrorCode:
    """Classify a Claude agent ``result.result`` text into a coarse error code.

    Returns one of:

    - ``"quota_exhausted"`` — matches "out of (extra) usage" (e.g.
      "You're out of extra usage · resets 3:10am").
    - ``"auth_required"`` — matches "authenticat" (authentication /
      authenticate).
    - ``"agent_timeout"`` — matches "timeout" / "timed out".
    - ``"agent_error"`` — fallback when nothing matches, or when ``text``
      is ``None`` / empty (e.g. crashed attempt with no result event).
    """
    if not text:
        return "agent_error"
    if _QUOTA_PATTERN.search(text):
        return "quota_exhausted"
    if _AUTH_PATTERN.search(text):
        return "auth_required"
    if _TIMEOUT_PATTERN.search(text):
        return "agent_timeout"
    return "agent_error"


class QuotaExhaustedError(RuntimeError):
    """Raised when a Claude agent invocation reports quota exhaustion.

    Carries the parsed reset time so the pool can pause-and-resume around
    the named window instead of crashing every queued attempt in 1.6s
    bursts (the 2026-05-13 cascade pattern).
    """

    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at
        self.message = message


def parse_quota_reset_clock(text: str | None) -> tuple[int, int, str] | None:
    """Parse the ``resets 3:10am`` clause from a quota-exhausted message.

    Returns ``(hour_12, minute, "am"|"pm")`` in the timezone Claude
    reports (typically the deployment's local TZ); timezone resolution
    is left to the caller. Returns ``None`` if no reset clause is
    present.
    """
    if not text:
        return None
    m = _RESET_PATTERN.search(text)
    if not m:
        return None
    hour = int(m.group("hour"))
    minute = int(m.group("minute") or "0")
    ampm = m.group("ampm").lower()
    return (hour, minute, ampm)


# Default timezone for quota-reset clock interpretation. Claude Code reports
# reset clocks in the deployment's local TZ; our ops runs on America/Los_Angeles.
_DEFAULT_RESET_TZ = "America/Los_Angeles"


def parse_quota_reset_at(
    text: str | None,
    *,
    now: datetime | None = None,
    tz: str = _DEFAULT_RESET_TZ,
) -> datetime | None:
    """Parse a "resets HH:MMam" clause into the next absolute tz-aware datetime.

    Returns the next datetime at the given clock time strictly after ``now``
    (so "resets 3:10am" at 02:32 PDT today resolves to 03:10 PDT today; at
    04:00 PDT it resolves to 03:10 PDT tomorrow). Returns ``None`` when no
    reset clause is present.

    Operators should treat the returned datetime as the earliest moment the
    pool can resume — add a small buffer (e.g. 30s) before lifting the pause.
    """
    clock = parse_quota_reset_clock(text)
    if clock is None:
        return None
    hour_12, minute, ampm = clock
    hour_24 = hour_12 % 12
    if ampm == "pm":
        hour_24 += 12
    zone = ZoneInfo(tz)
    base_now = (now or datetime.now(zone)).astimezone(zone)
    candidate = base_now.replace(hour=hour_24, minute=minute, second=0, microsecond=0)
    if candidate <= base_now:
        candidate = candidate + timedelta(days=1)
    return candidate
