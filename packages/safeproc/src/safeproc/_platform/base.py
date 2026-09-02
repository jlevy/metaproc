"""The platform contract: capabilities, not guesses.

A provider supplies host evidence, the process table, attributable process cost, and
signalling. The core consumes the capability record to know what each field means and
which scope it has; unsupported evidence is explicit, never approximated without a label.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from safeproc.identity import ProcessRecord
from safeproc.models import HostSample, PlatformName


class UnsupportedPlatformError(RuntimeError):
    """Raised when no provider can read this host's gauges. A wrong answer is worse."""


@dataclass(frozen=True)
class Capabilities:
    """What this provider can measure, and how. Written to every session record."""

    platform: PlatformName
    host_budget: str
    """Where ``reclaimable_gb`` comes from."""

    alarm: str
    """Where the normalized 1/2/4 pressure alarm comes from."""

    process_cost: str
    """The attributable per-process metric behind ``costs``."""

    degradation: str
    """The predictive and measured degradation signals available."""

    psi: str
    """Linux: ``absent``, ``averages``, or ``triggers``. Elsewhere ``n/a``."""

    cgroup_headroom: bool
    """Whether the budget is bounded by the caller's own cgroup."""

    swap_volume: str
    """The volume swap grows on when that matters, else ``n/a``."""

    sleep_clock: str
    identity: str
    sampling: str
    """``native`` when the hot path forks nothing; ``helper`` when it runs commands."""

    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "platform": str(self.platform),
            "host_budget": self.host_budget,
            "alarm": self.alarm,
            "process_cost": self.process_cost,
            "degradation": self.degradation,
            "psi": self.psi,
            "cgroup_headroom": self.cgroup_headroom,
            "swap_volume": self.swap_volume,
            "sleep_clock": self.sleep_clock,
            "identity": self.identity,
            "sampling": self.sampling,
            "notes": list(self.notes),
        }


class Provider(Protocol):
    """One platform's evidence and signalling."""

    def capabilities(self) -> Capabilities: ...

    def host_sample(self) -> HostSample: ...

    def process_table(self) -> list[ProcessRecord]: ...

    def discovery_table(self) -> list[ProcessRecord]:
        """The table with full argv, for one-off pattern discovery only.

        May run a helper command; it is never on the sampling path. On Linux it is the
        ordinary table.
        """
        ...

    def costs(self, pids: Sequence[int], min_mb: float) -> dict[int, float]:
        """Attributable cost in MB for the given PIDs at or above ``min_mb``.

        Absent means below the floor, exited, or not this user's process. Callers fall
        back to RSS, which is wrong but never worse than nothing.
        """
        ...

    def signal(self, pid: int, sig: int) -> bool:
        """Send one signal. ``False`` when the PID is gone or not ours. Never raises."""
        ...

    def alive(self, pid: int) -> bool:
        """Whether ``pid`` exists as something that can hold memory. Zombies are not alive."""
        ...

    def current_uid(self) -> int: ...

    def machine_facts(self) -> Mapping[str, object]: ...

    def harden_scheduling(self) -> str:
        """Raise the sentinel's own priority where the platform allows. Reports what happened."""
        ...


def get_provider() -> Provider:
    """The provider for this host, or ``UnsupportedPlatformError``."""
    # Providers import lazily so the pure core and the other platform's module never
    # load on the sampling path; the import boundary test relies on this.
    if sys.platform == "linux":
        from safeproc._platform.linux import LinuxProvider  # noqa: PLC0415 -- platform guard

        return LinuxProvider()
    if sys.platform == "darwin":
        from safeproc._platform.darwin import DarwinProvider  # noqa: PLC0415 -- platform guard

        return DarwinProvider()
    raise UnsupportedPlatformError(
        f"safeproc supports macOS and Linux; this host reports {sys.platform!r}. "
        "Windows is deferred; see the RunPool host-safety plan."
    )
