"""The platform-neutral parts of the macOS provider. Native calls are validated on macOS."""

from __future__ import annotations

import os
import sys

import pytest

from safeproc._platform.darwin import DarwinProvider, parse_etime, parse_ps_table


def test_parse_etime() -> None:
    assert parse_etime("05:07") == 307
    assert parse_etime("01:02:03") == 3723
    assert parse_etime("2-01:02:03") == 176523
    assert parse_etime("garbage") == 0


def test_parse_ps_table_keeps_zombies_with_state() -> None:
    text = (
        "  100     1 Ss     501  40000 01:00:00 /usr/bin/python3 coordinator\n"
        "  101   100 R+     501 900000    05:00 node agent --worker\n"
        "  102   100 Z      501      0    00:10 (node)\n"
        "garbage line\n"
    )
    rows = parse_ps_table(text, now_epoch=1_000_000.0)
    assert [r.pid for r in rows] == [100, 101, 102]
    worker = rows[1]
    assert worker.ppid == 100
    assert worker.rss_mb == 900000 / 1024
    assert worker.age_s == 300.0
    assert worker.create_token == 1_000_000 - 300
    assert worker.cmd == "node agent --worker"
    assert rows[2].is_zombie


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin readings")
def test_native_readings_are_plausible() -> None:
    """HANDOFF: the first native check. A wrong ctypes layout fails here, not in a guard."""
    provider = DarwinProvider()
    caps = provider.capabilities()
    assert caps.sampling == "native", caps
    sample = provider.host_sample()
    assert sample.total_gb is not None and sample.total_gb > 1.0
    assert 0.0 < sample.reclaimable_gb < sample.total_gb
    assert sample.pressure in {1, 2, 4}
    assert 0.0 < sample.ancm_ratio <= 1.0
    assert sample.disk_gb > 0.0
    table = {r.pid: r for r in provider.process_table()}
    me = table[os.getpid()]
    assert me.ppid == os.getppid()
    assert me.uid == os.getuid()
    assert me.create_token > 0
    assert me.age_s >= 0.0
    costs = provider.costs([os.getpid()], min_mb=0.0)
    assert costs[os.getpid()] > 1.0
    assert provider.alive(os.getpid())
    assert provider.harden_scheduling() in {
        "hardened",
        "unavailable",
    } or provider.harden_scheduling().startswith("partial")
