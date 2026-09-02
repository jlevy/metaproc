"""Shared fixtures: a deterministic provider, clock, and process trees."""

from __future__ import annotations

import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest

from safeproc._platform.base import Capabilities
from safeproc.clocks import FakeClock
from safeproc.identity import ProcessRecord
from safeproc.models import ALARM_NORMAL, HostSample, PlatformName


def host(
    reclaimable_gb: float = 12.0,
    pressure: int = ALARM_NORMAL,
    *,
    compressed_gb: float = 4.0,
    disk_gb: float = 200.0,
    ancm_ratio: float = 0.8,
    swap_total_mb: float = 2048.0,
    swap_used_mb: float = 512.0,
    stall_full_pct: float | None = None,
    stall_some_pct: float | None = None,
    platform: PlatformName = PlatformName.FAKE,
) -> HostSample:
    return HostSample(
        platform=platform,
        reclaimable_gb=reclaimable_gb,
        free_gb=reclaimable_gb / 3,
        pressure=pressure,
        wired_gb=3.0,
        compressed_gb=compressed_gb,
        swap_used_mb=swap_used_mb,
        swap_total_mb=swap_total_mb,
        disk_gb=disk_gb,
        ancm_ratio=ancm_ratio,
        total_gb=34.0,
        stall_full_pct=stall_full_pct,
        stall_some_pct=stall_some_pct,
    )


def row(
    pid: int,
    ppid: int,
    *,
    rss_mb: float = 50.0,
    footprint_mb: float = 0.0,
    age_s: float = 30.0,
    cmd: str = "worker",
    state: str = "S",
    uid: int = 1000,
    token: int | None = None,
) -> ProcessRecord:
    return ProcessRecord(
        pid=pid,
        ppid=ppid,
        uid=uid,
        state=state,
        rss_mb=rss_mb,
        age_s=age_s,
        cmd=cmd,
        create_token=pid * 1000 if token is None else token,
        footprint_mb=footprint_mb,
    )


def tree_table() -> list[ProcessRecord]:
    """root 100 -> orchestrator 101 -> workers 102..105; a shim 106 forks worker 107."""
    return [
        row(1, 0, cmd="init", uid=0),
        row(100, 1, cmd="coordinator", age_s=300),
        row(101, 100, cmd="orchestrator", age_s=200),
        row(102, 101, rss_mb=900, cmd="agent worker a"),
        row(103, 101, rss_mb=1200, cmd="agent worker b"),
        row(104, 101, rss_mb=700, cmd="agent worker c"),
        row(105, 101, rss_mb=40, cmd="agent shim"),
        row(106, 100, rss_mb=45, cmd="agent shim"),
        row(107, 106, rss_mb=2500, cmd="agent worker d", age_s=12),
        row(200, 1, rss_mb=3000, cmd="unrelated app"),
        row(201, 100, rss_mb=100, cmd="dead", state="Z"),
    ]


@dataclass
class FakeProvider:
    """A scripted platform: the process table and host samples tests hand it."""

    table: list[ProcessRecord] = field(default_factory=tree_table)
    hosts: list[HostSample] = field(default_factory=list)
    footprints: dict[int, float] = field(default_factory=dict)
    signals: list[tuple[int, int]] = field(default_factory=list)
    uid: int = 1000
    denied: set[int] = field(default_factory=set)
    kill_on_term: bool = True

    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=PlatformName.FAKE,
            host_budget="scripted",
            alarm="scripted",
            process_cost="scripted footprints",
            degradation="scripted",
            psi="n/a",
            cgroup_headroom=False,
            swap_volume="n/a",
            sleep_clock="fake",
            identity="pid plus fake token",
            sampling="native",
        )

    def host_sample(self) -> HostSample:
        if len(self.hosts) > 1:
            return self.hosts.pop(0)
        if self.hosts:
            return self.hosts[0]
        return host()

    def process_table(self) -> list[ProcessRecord]:
        return list(self.table)

    def discovery_table(self) -> list[ProcessRecord]:
        return list(self.table)

    def costs(self, pids: Sequence[int], min_mb: float) -> dict[int, float]:
        found: dict[int, float] = {}
        for pid in pids:
            mb = self.footprints.get(pid)
            if mb is None:
                current = next((r for r in self.table if r.pid == pid), None)
                mb = current.rss_mb if current is not None else None
            if mb is not None and mb >= min_mb:
                found[pid] = mb
        return found

    def signal(self, pid: int, sig: int) -> bool:
        if pid in self.denied:
            return False
        if not any(r.pid == pid for r in self.table):
            return False
        self.signals.append((pid, sig))
        if sig in (signal.SIGKILL, signal.SIGTERM) and self.kill_on_term:
            self.table = [r for r in self.table if r.pid != pid]
        return True

    def alive(self, pid: int) -> bool:
        return any(r.pid == pid and not r.is_zombie for r in self.table)

    def current_uid(self) -> int:
        return self.uid

    def machine_facts(self) -> Mapping[str, object]:
        return {"host": "fake", "ram_gb": 34.0}

    def harden_scheduling(self) -> str:
        return "fake"

    def sent(self, sig: int) -> list[int]:
        return [pid for pid, s in self.signals if s == sig]


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(current=1000.0)
