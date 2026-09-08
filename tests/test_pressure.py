# pyright: reportUnusedVariable=false, reportUnusedImport=false
"""Tests for metaproc.osutils.memory_pressure — cross-platform memory pressure measurement."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from metaproc.osutils.memory_pressure import (
    MemoryPressure,
    PressureLevel,
    UnsupportedTelemetryPlatformError,
    _classify,
    _measure_linux,
    _measure_macos,
    _parse_macos_reclaimable_bytes,
    _parse_macos_swap_used_gb,
    adapt_batch_size,
    classify_swap_rate,
    measure,
    validate_supported_platform,
)


class TestClassify:
    def test_normal(self) -> None:
        assert _classify(26, total_memory_gb=32) == PressureLevel.NORMAL
        assert _classify(50, total_memory_gb=32) == PressureLevel.NORMAL
        assert _classify(100, total_memory_gb=32) == PressureLevel.NORMAL

    def test_elevated(self) -> None:
        assert _classify(25, total_memory_gb=32) == PressureLevel.ELEVATED
        assert _classify(16, total_memory_gb=32) == PressureLevel.ELEVATED

    def test_high(self) -> None:
        assert _classify(15, total_memory_gb=32) == PressureLevel.HIGH
        assert _classify(9, total_memory_gb=32) == PressureLevel.HIGH

    def test_critical(self) -> None:
        assert _classify(8, total_memory_gb=32) == PressureLevel.CRITICAL
        assert _classify(0, total_memory_gb=32) == PressureLevel.CRITICAL
        assert _classify(5, total_memory_gb=32) == PressureLevel.CRITICAL

    def test_swap_rate_uses_total_memory_ratio(self) -> None:
        assert classify_swap_rate(0.2, total_memory_gb=32) == PressureLevel.NORMAL
        assert classify_swap_rate(0.4, total_memory_gb=32) == PressureLevel.ELEVATED
        assert classify_swap_rate(1.0, total_memory_gb=32) == PressureLevel.HIGH
        assert classify_swap_rate(3.3, total_memory_gb=32) == PressureLevel.CRITICAL

    def test_macos_swap_parser(self) -> None:
        text = "total = 41984.00M  used = 40829.19M  free = 1154.81M  (encrypted)"
        assert _parse_macos_swap_used_gb(text) == pytest.approx(39.87, abs=0.01)


# Real `vm_stat` output, 16 KiB pages. Active is deliberately large: it is the
# bucket that must NOT reach the budget.
VM_STAT_16K = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                4115.
Pages active:                            493792.
Pages inactive:                          465749.
Pages speculative:                        26981.
Pages throttled:                              0.
Pages wired down:                        328108.
Pages purgeable:                           4097.
"Translation faults":                 987654321.
Pages copy-on-write:                   12345678.
Pages zero filled:                    123456789.
Pages reactivated:                      1234567.
Pages purged:                            123456.
File-backed pages:                       234567.
Anonymous pages:                         345678.
Pages stored in compressor:             1234567.
Pages occupied by compressor:            737026.
"""


class TestMacosReclaimable:
    def test_sums_free_inactive_and_purgeable(self) -> None:
        expected = (4115 + 465749 + 4097) * 16384
        assert _parse_macos_reclaimable_bytes(VM_STAT_16K) == expected

    def test_excludes_active_pages(self) -> None:
        """Active pages are other processes' working sets.

        Counting them is precisely what makes kern.memorystatus_level unusable as a
        budget: on the host this fixture came from it reported roughly twice the
        reclaimable figure. A regression here is a 2x over-grant, so it is asserted
        directly rather than implied by the sum above.
        """
        reclaimable = _parse_macos_reclaimable_bytes(VM_STAT_16K)
        with_active = reclaimable + 493792 * 16384
        assert reclaimable < with_active / 1.9

    def test_honours_the_reported_page_size(self) -> None:
        """Intel hosts report 4096; the page size is parsed, never assumed."""
        text = VM_STAT_16K.replace("page size of 16384 bytes", "page size of 4096 bytes")
        assert _parse_macos_reclaimable_bytes(text) == (4115 + 465749 + 4097) * 4096

    def test_missing_page_size_is_an_error(self) -> None:
        text = VM_STAT_16K.replace("(page size of 16384 bytes)", "")
        with pytest.raises(UnsupportedTelemetryPlatformError, match="page size"):
            _parse_macos_reclaimable_bytes(text)

    def test_missing_bucket_is_an_error(self) -> None:
        """A silently absent bucket would understate the budget, not overstate it,
        but it still means the host is not the platform this code was written for."""
        text = "\n".join(
            line for line in VM_STAT_16K.splitlines() if not line.startswith("Pages inactive")
        )
        with pytest.raises(UnsupportedTelemetryPlatformError, match="Pages inactive"):
            _parse_macos_reclaimable_bytes(text)

    def test_non_positive_page_size_is_an_error(self) -> None:
        text = VM_STAT_16K.replace("page size of 16384 bytes", "page size of 0 bytes")
        with pytest.raises(UnsupportedTelemetryPlatformError, match="page size"):
            _parse_macos_reclaimable_bytes(text)


class TestMeasureMacos:
    """The gauge and the budget must not be the same number.

    kern.memorystatus_level is retained for its alarm value, but reading it as
    headroom over-grants by roughly 2x, which is what these fixtures encode: the
    VM_STAT_16K buckets are the snapshot that produced a 48.0 gauge reading, against
    a 22.6% reclaimable budget on the same host at the same moment.
    """

    @staticmethod
    def _stub(args: list[str]) -> str:
        if args[0] == "vm_stat":
            return VM_STAT_16K
        if args[-1] == "hw.memsize":
            return str(34_359_738_368)
        if args[-1] == "kern.memorystatus_level":
            return "48.0"
        if args[-1] == "vm.swapusage":
            return "total = 2048.00M  used = 1024.00M  free = 1024.00M  (encrypted)"
        raise AssertionError(f"unexpected telemetry call: {args}")

    def test_available_pct_is_the_reclaimable_budget(self) -> None:
        with patch("metaproc.osutils.memory_pressure._read_command_stdout", side_effect=self._stub):
            pressure = _measure_macos()
        expected = (4115 + 465749 + 4097) * 16384 / 34_359_738_368 * 100
        assert pressure.available_pct == pytest.approx(expected, abs=0.01)
        assert pressure.available_pct == pytest.approx(22.60, abs=0.05)

    def test_the_gauge_is_reported_separately_as_an_alarm(self) -> None:
        with patch("metaproc.osutils.memory_pressure._read_command_stdout", side_effect=self._stub):
            pressure = _measure_macos()
        assert pressure.alarm_pct is not None
        assert pressure.alarm_pct == 48.0
        assert pressure.available_pct < pressure.alarm_pct / 1.9
        assert pressure.source == "macos-vm-stat"

    def test_level_derives_from_the_budget_not_the_gauge(self) -> None:
        """At 22.6% reclaimable this host is already ELEVATED; the gauge's 48% would
        read NORMAL and keep ramping, which is the failure this guards."""
        with patch("metaproc.osutils.memory_pressure._read_command_stdout", side_effect=self._stub):
            pressure = _measure_macos()
        assert pressure.level == PressureLevel.ELEVATED
        assert pressure.alarm_pct is not None
        assert _classify(pressure.alarm_pct, total_memory_gb=32.0) == PressureLevel.NORMAL

    def test_an_out_of_range_gauge_still_fails_loudly(self) -> None:
        def stub(args: list[str]) -> str:
            if args[-1] == "kern.memorystatus_level":
                return "127.0"
            return self._stub(args)

        with (
            patch("metaproc.osutils.memory_pressure._read_command_stdout", side_effect=stub),
            pytest.raises(UnsupportedTelemetryPlatformError, match="out of range"),
        ):
            _measure_macos()


class TestMemoryPressureStr:
    def test_str_format(self) -> None:
        p = MemoryPressure(
            available_pct=57.0,
            swap_used_gb=9.8,
            total_memory_gb=32.0,
            level=PressureLevel.NORMAL,
            source="macos-memorystatus",
        )
        s = str(p)
        assert "57%" in s
        assert "normal" in s
        assert "9.8 GB" in s
        assert "32.0 GB" in s


class TestMeasureLinux:
    def test_parses_proc_meminfo(self, tmp_path: Path) -> None:
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(
            "MemTotal:       16384000 kB\n"
            "MemFree:         1024000 kB\n"
            "MemAvailable:    8192000 kB\n"
            "SwapTotal:       4096000 kB\n"
            "SwapFree:        2048000 kB\n"
        )
        with patch("metaproc.osutils.memory_pressure.Path") as mock_path:
            # /proc/meminfo exists and returns our test data
            _ = mock_path.return_value
            # We need to handle two Path() calls: /proc/meminfo and /proc/pressure/memory
            instances = {
                "/proc/meminfo": type(
                    "P",
                    (),
                    {
                        "exists": lambda self: True,
                        "read_text": lambda self: meminfo.read_text(),
                    },
                )(),
                "/proc/pressure/memory": type(
                    "P",
                    (),
                    {
                        "exists": lambda self: False,
                    },
                )(),
            }
            mock_path.side_effect = lambda p: instances.get(
                p, type("P", (), {"exists": lambda self: False})()
            )
            result = _measure_linux()

        assert result.available_pct == pytest.approx(50.0, abs=0.1)
        # (4096000 - 2048000) kB / (1024 * 1024) = 1.953125 GB
        assert result.swap_used_gb == pytest.approx(1.953125, abs=0.01)
        assert result.total_memory_gb == pytest.approx(15.625, abs=0.01)
        assert result.level == PressureLevel.NORMAL  # 50% > 25% NORMAL threshold

    def test_with_psi_lowers_available(self, tmp_path: Path) -> None:
        meminfo_text = (
            "MemTotal:       16384000 kB\n"
            "MemAvailable:    8192000 kB\n"
            "SwapTotal:       4096000 kB\n"
            "SwapFree:        4096000 kB\n"
        )
        # PSI avg10=40 → psi_available = 100 - 40*2 = 20, lower than meminfo's 50%
        psi_text = "some avg10=40.00 avg60=10.00 avg300=5.00 total=123456\n"

        instances = {
            "/proc/meminfo": type(
                "P",
                (),
                {
                    "exists": lambda self: True,
                    "read_text": lambda self: meminfo_text,
                },
            )(),
            "/proc/pressure/memory": type(
                "P",
                (),
                {
                    "exists": lambda self: True,
                    "read_text": lambda self: psi_text,
                },
            )(),
        }
        with patch(
            "metaproc.osutils.memory_pressure.Path",
            side_effect=lambda p: instances.get(p, type("P", (), {"exists": lambda self: False})()),
        ):
            result = _measure_linux()

        # PSI avg10=40 → psi_available = 20, which is lower than meminfo's 50%.
        # 20% lands in the ELEVATED band (15-25%) under the workstation-tuned
        # thresholds.
        assert result.available_pct == pytest.approx(20.0, abs=0.1)
        assert "psi" in result.source
        assert result.level == PressureLevel.ELEVATED


class TestAdaptBatchSize:
    def test_normal_returns_original(self) -> None:
        assert adapt_batch_size(50, PressureLevel.NORMAL) == 50

    def test_elevated_returns_original(self) -> None:
        assert adapt_batch_size(50, PressureLevel.ELEVATED) == 50

    def test_high_halves_batch_size(self) -> None:
        assert adapt_batch_size(50, PressureLevel.HIGH) == 25

    def test_critical_quarters_batch_size(self) -> None:
        assert adapt_batch_size(40, PressureLevel.CRITICAL) == 10

    def test_never_below_one(self) -> None:
        assert adapt_batch_size(1, PressureLevel.HIGH) == 1
        assert adapt_batch_size(1, PressureLevel.CRITICAL) == 1
        assert adapt_batch_size(2, PressureLevel.CRITICAL) == 1

    def test_rounds_down(self) -> None:
        assert adapt_batch_size(3, PressureLevel.HIGH) == 1
        assert adapt_batch_size(7, PressureLevel.CRITICAL) == 1


class TestMeasure:
    def test_returns_memory_pressure(self) -> None:
        result = measure()
        assert isinstance(result, MemoryPressure)
        assert 0 <= result.available_pct <= 100
        assert result.swap_used_gb >= 0
        assert result.total_memory_gb > 0
        assert isinstance(result.level, PressureLevel)
        assert result.source  # non-empty

    def test_str_is_readable(self) -> None:
        result = measure()
        s = str(result)
        assert "%" in s
        assert "GB" in s

    def test_validate_supported_platform_names_unsupported_platform(self) -> None:
        with patch("metaproc.osutils.memory_pressure.platform.system", return_value="Plan9"):
            with pytest.raises(UnsupportedTelemetryPlatformError, match="unsupported runpool"):
                validate_supported_platform()
