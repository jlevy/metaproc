"""The Linux provider against a fake procfs. Parsing is platform-neutral."""

from __future__ import annotations

import os
from pathlib import Path

from safeproc._platform.linux import (
    LinuxAlarmConfig,
    LinuxProvider,
    derive_alarm,
    parse_cgroup_path,
    parse_meminfo,
    parse_psi,
    parse_stat,
)
from safeproc.models import ALARM_CRITICAL, ALARM_NORMAL, ALARM_WARNING, PlatformName

MEMINFO = """MemTotal:       32768000 kB
MemFree:         2048000 kB
MemAvailable:   12288000 kB
Buffers:          100000 kB
SwapTotal:       8192000 kB
SwapFree:        6144000 kB
Zswapped:         512000 kB
"""

PSI = """some avg10=3.50 avg60=1.00 avg300=0.50 total=12345
full avg10=0.75 avg60=0.10 avg300=0.05 total=678
"""


def test_parse_meminfo() -> None:
    info = parse_meminfo(MEMINFO)
    assert info["MemAvailable"] == 12288000
    assert info["Zswapped"] == 512000
    assert "Buffers" not in info


def test_parse_psi() -> None:
    assert parse_psi(PSI) == (3.5, 0.75)
    assert parse_psi("") == (None, None)


def test_parse_stat_handles_spaces_and_parens_in_comm() -> None:
    line = "4242 (a (weird) name) S 100 4242 4242 0 -1 4194560 100 0 0 0 5 3 0 0 20 0 1 0 987654 12345678 2000 18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 3 0 0 0 0 0\n"
    parsed = parse_stat(line)
    assert parsed == ("a (weird) name", "S", 100, 987654, 2000)
    assert parse_stat("garbage") is None


def test_parse_cgroup_path() -> None:
    assert parse_cgroup_path("0::/user.slice/user-1000.slice/session-1.scope\n") == (
        "/user.slice/user-1000.slice/session-1.scope"
    )
    assert parse_cgroup_path("12:memory:/foo\n") is None


def test_derive_alarm_from_stall_and_fraction() -> None:
    cfg = LinuxAlarmConfig()
    assert (
        derive_alarm(available_fraction=0.5, stall_some_pct=None, stall_full_pct=None, config=cfg)
        == ALARM_NORMAL
    )
    assert (
        derive_alarm(available_fraction=0.5, stall_some_pct=5.0, stall_full_pct=0.0, config=cfg)
        == ALARM_WARNING
    )
    assert (
        derive_alarm(available_fraction=0.5, stall_some_pct=5.0, stall_full_pct=20.0, config=cfg)
        == ALARM_CRITICAL
    )
    assert (
        derive_alarm(available_fraction=0.05, stall_some_pct=None, stall_full_pct=None, config=cfg)
        == ALARM_CRITICAL
    )
    assert (
        derive_alarm(available_fraction=0.10, stall_some_pct=None, stall_full_pct=None, config=cfg)
        == ALARM_WARNING
    )


def _fake_proc(root: Path, *, with_cgroup: bool) -> tuple[Path, Path]:
    proc = root / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text(MEMINFO)
    (proc / "uptime").write_text("5000.00 20000.00\n")
    (proc / "vmstat").write_text("pswpin 100\npswpout 50\n")
    (proc / "pressure").mkdir()
    (proc / "pressure" / "memory").write_text(PSI)
    (proc / "self").mkdir()
    (proc / "self" / "cgroup").write_text("0::/test.slice\n")

    def pid(
        number: int,
        ppid: int,
        comm: str,
        state: str,
        start_ticks: int,
        rss_pages: int,
        cmdline: bytes,
    ) -> None:
        d = proc / str(number)
        d.mkdir()
        fields = ["0"] * 52
        fields[0] = state
        fields[1] = str(ppid)
        fields[19] = str(start_ticks)
        fields[21] = str(rss_pages)
        (d / "stat").write_text(f"{number} ({comm}) " + " ".join(fields) + "\n")
        (d / "cmdline").write_bytes(cmdline)
        (d / "smaps_rollup").write_text(f"Rss:   {rss_pages * 4} kB\nPss:   {rss_pages * 3} kB\n")

    pid(1, 0, "init", "S", 10, 500, b"/sbin/init\0")
    pid(100, 1, "coord", "S", 400_000, 20_000, b"python\0coordinator\0")
    pid(101, 100, "worker", "R", 480_000, 300_000, b"node\0agent\0--worker\0")
    pid(102, 100, "zombie", "Z", 490_000, 0, b"")
    cgroup = root / "cgroup"
    cgroup.mkdir()
    if with_cgroup:
        (cgroup / "cgroup.controllers").write_text("memory\n")
        slice_dir = cgroup / "test.slice"
        slice_dir.mkdir()
        (slice_dir / "memory.max").write_text("4294967296\n")
        (slice_dir / "memory.current").write_text("1073741824\n")
        (slice_dir / "memory.pressure").write_text(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        )
    return proc, cgroup


def test_provider_reads_a_fake_procfs(tmp_path: Path) -> None:
    proc, cgroup = _fake_proc(tmp_path, with_cgroup=False)
    provider = LinuxProvider(proc, cgroup)
    caps = provider.capabilities()
    assert caps.platform is PlatformName.LINUX
    assert caps.psi == "averages"
    assert caps.cgroup_headroom is False
    assert caps.sampling == "native"

    sample = provider.host_sample()
    assert abs(sample.reclaimable_gb - 12288000 * 1024 / 1e9) < 1e-6
    assert sample.stall_some_pct == 3.5
    assert sample.stall_full_pct == 0.75
    assert sample.pressure == ALARM_WARNING  # some stall above the warning threshold
    assert sample.swap_used_mb == 2000.0
    assert sample.cgroup_headroom_gb is None
    assert sample.swapin_rate_per_s == 0.0

    table = {r.pid: r for r in provider.process_table()}
    assert set(table) == {1, 100, 101, 102}
    worker = table[101]
    assert worker.ppid == 100
    assert worker.cmd == "node agent --worker"
    assert worker.create_token == 480_000
    assert worker.uid == os.getuid()
    assert 0 < worker.age_s < 5000
    assert table[102].is_zombie
    assert provider.alive(101)
    assert not provider.alive(102)
    assert not provider.alive(999)

    costs = provider.costs([100, 101], min_mb=100.0)
    assert 101 in costs and 100 not in costs
    assert costs[101] == 300_000 * 3 / 1024

    facts = provider.machine_facts()
    assert facts["psi"] == "averages"


def test_provider_bounds_the_budget_by_cgroup_headroom(tmp_path: Path) -> None:
    proc, cgroup = _fake_proc(tmp_path, with_cgroup=True)
    provider = LinuxProvider(proc, cgroup)
    caps = provider.capabilities()
    assert caps.cgroup_headroom is True
    sample = provider.host_sample()
    headroom_gb = (4294967296 - 1073741824) / 1e9
    assert sample.cgroup_headroom_gb is not None
    assert abs(sample.cgroup_headroom_gb - headroom_gb) < 1e-9
    assert abs(sample.reclaimable_gb - headroom_gb) < 1e-9
    # The cgroup-local pressure file is preferred and reads no stall.
    assert sample.stall_some_pct == 0.0
    assert sample.pressure == ALARM_NORMAL
