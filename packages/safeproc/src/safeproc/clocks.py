"""Clock domains.

Every deadline names its clock. Two domains matter: an *active* clock that stops while
the host sleeps, for work that cannot progress during sleep such as a startup window, and
a *sleep-aware* clock that keeps counting, for operator wall deadlines. Confusing them is
how a startup reservation expires because a laptop closed its lid.

On Linux the sleep-aware clock is ``CLOCK_BOOTTIME`` and the active clock is
``CLOCK_MONOTONIC``. On macOS ``time.monotonic`` is ``mach_absolute_time``, which excludes
sleep, and ``CLOCK_MONOTONIC`` through ``clock_gettime`` continues across sleep, which is
the documented behavior of that clock on Darwin and matches ``mach_continuous_time``.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class ClockDomain(StrEnum):
    """Which clock a timestamp or deadline is measured on."""

    ACTIVE = "active"
    """Stops during system sleep. For work that cannot progress while asleep."""

    SLEEP_AWARE = "sleep_aware"
    """Keeps counting during system sleep. For operator wall deadlines."""

    WALL = "wall"
    """Civil time. For journal timestamps only; never for a deadline."""


class Clock(Protocol):
    """A source of seconds on one clock domain."""

    @property
    def domain(self) -> ClockDomain: ...

    def now(self) -> float: ...


@dataclass(frozen=True)
class ActiveClock:
    """Seconds that stop during system sleep."""

    domain: ClockDomain = field(default=ClockDomain.ACTIVE, init=False)

    def now(self) -> float:
        return time.monotonic()


def _sleep_aware_now() -> float:
    if sys.platform == "linux":
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    if sys.platform == "darwin":
        return time.clock_gettime(time.CLOCK_MONOTONIC)
    return time.monotonic()  # pragma: no cover - other platforms


@dataclass(frozen=True)
class SleepAwareClock:
    """Seconds that keep counting during system sleep."""

    domain: ClockDomain = field(default=ClockDomain.SLEEP_AWARE, init=False)

    def now(self) -> float:
        return _sleep_aware_now()


@dataclass
class FakeClock:
    """A clock tests drive by hand. Time moves only when told to."""

    current: float = 0.0
    domain: ClockDomain = ClockDomain.ACTIVE

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("a fake clock only moves forward")
        self.current += seconds
        return self.current


@dataclass(frozen=True)
class Deadline:
    """A moment on a named clock. Comparing across domains is an error, not a guess."""

    at: float
    domain: ClockDomain

    def reached(self, clock: Clock) -> bool:
        if clock.domain is not self.domain:
            raise ValueError(f"deadline on {self.domain} compared with a {clock.domain} clock")
        return clock.now() >= self.at
