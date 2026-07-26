"""MockBackend — in-memory backend for deterministic testing.

Implements the ``LaunchBackend`` protocol without spawning real processes
or making cloud API calls.  Behaviors are configured per-label so tests
can mix success, failure, and preemption scenarios.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from metaproc.runpool.backend import HealthMetrics, LaunchHandle, PreparedLaunch


@dataclass
class MockBehavior:
    """Configures how MockBackend simulates a single process."""

    exit_code: int = 0
    startup_delay_s: float = 0.0
    run_duration_s: float = 0.01
    rss_bytes: int = 100_000_000
    descendants: int = 1
    log_output: str = "mock log output"


@dataclass
class MockBackend:
    """In-memory backend for deterministic testing."""

    behaviors: dict[str, MockBehavior] = field(default_factory=dict)
    default_behavior: MockBehavior = field(default_factory=MockBehavior)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    _launch_times: dict[str, float] = field(default_factory=dict)
    _killed: set[str] = field(default_factory=set)
    _started: set[str] = field(default_factory=set)

    @property
    def name(self) -> str:
        return "mock"

    def _behavior(self, label: str) -> MockBehavior:
        return self.behaviors.get(label, self.default_behavior)

    async def launch(
        self,
        prepared: PreparedLaunch,
        label: str = "",
    ) -> LaunchHandle:
        self.calls.append(("launch", label))
        behavior = self._behavior(label)

        if behavior.startup_delay_s > 0:
            await asyncio.sleep(behavior.startup_delay_s)

        self._launch_times[label] = time.monotonic()
        self._started.add(label)

        return LaunchHandle(
            pid=None,
            external_id=f"mock-{label}",
            backend_name="mock",
            metadata={"label": label},
        )

    async def poll(self, handle: LaunchHandle) -> int | None:
        label = (handle.metadata or {}).get("label", "")
        self.calls.append(("poll", label))

        if label in self._killed:
            return 137  # SIGKILL

        behavior = self._behavior(label)
        launch_time = self._launch_times.get(label)
        if launch_time is None:
            return 1  # Never launched

        elapsed = time.monotonic() - launch_time
        if elapsed < behavior.run_duration_s:
            return None  # Still running

        return behavior.exit_code

    async def kill(self, handle: LaunchHandle, sig: int | None = None) -> None:
        label = (handle.metadata or {}).get("label", "")
        self.calls.append(("kill", label))
        self._killed.add(label)

    async def health(self, handle: LaunchHandle) -> HealthMetrics | None:
        label = (handle.metadata or {}).get("label", "")
        self.calls.append(("health", label))

        if label in self._killed or label not in self._started:
            return None

        behavior = self._behavior(label)
        return HealthMetrics(
            rss_bytes=behavior.rss_bytes,
            descendants=behavior.descendants,
        )

    async def read_log_tail(self, handle: LaunchHandle, lines: int = 50) -> str:
        label = (handle.metadata or {}).get("label", "")
        self.calls.append(("read_log_tail", label))
        behavior = self._behavior(label)
        return behavior.log_output
