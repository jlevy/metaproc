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
