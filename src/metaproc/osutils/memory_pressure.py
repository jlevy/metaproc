# pyright: reportImplicitOverride=false
"""Cross-platform memory pressure measurement.

Provides a single ``measure()`` call that returns a normalized pressure
reading on supported macOS and Linux hosts without external dependencies.

macOS: reads ``kern.memorystatus_level`` via sysctl — the kernel's own
percentage of "free" memory (matches ``memory_pressure`` output).

Linux: computes ``MemAvailable / MemTotal`` from ``/proc/meminfo``.
Optionally reads PSI from ``/proc/pressure/memory`` when available.

Required platform telemetry must be readable. A runpool that cannot read
physical RAM, available memory, or swap counters is running on an unsupported
or broken host and should fail visibly instead of inventing a safe-looking
pressure reading.
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

log = logging.getLogger(__name__)


class UnsupportedTelemetryPlatformError(RuntimeError):
    """Raised when required runpool resource telemetry is unavailable."""


class PressureLevel(StrEnum):
    # Thresholds sized for a large workflow-style agent batch on a workstation:
    # claude/codex agents at ~500 MB-1 GB RSS, target conc=8-10. Operating
    # state on a 32 GB box with VS Code + browser open is ~30-50% free; the
    # thresholds keep that range in NORMAL (ramp up), and only treat memory
    # as constrained when free drops into territory where one more agent
    # would actually OOM (15-20% on a 32 GB box ≈ 5-6 GB free, ≈ 10-12
    # agents' worth of RSS).
    NORMAL = "normal"  # >25% available — ramp concurrency up
    ELEVATED = "elevated"  # 15-25% — hold current concurrency
    HIGH = "high"  # 8-15% — reduce concurrency
    CRITICAL = "critical"  # <8% — do not launch new work


@dataclass(frozen=True)
class MemoryPressure:
    available_pct: float  # 0-100, higher = more headroom
    swap_used_gb: float  # absolute swap usage in GB
    total_memory_gb: float  # physical or usable system RAM in GB
    level: PressureLevel  # derived threshold level
    source: str  # diagnostic: which backend was used

    def __str__(self) -> str:
        return (
            f"memory: {self.available_pct:.0f}% available ({self.level.value}), "
            f"swap {self.swap_used_gb:.1f} GB, RAM {self.total_memory_gb:.1f} GB "
            f"[{self.source}]"
        )


_PRESSURE_RANK: dict[PressureLevel, int] = {
    PressureLevel.NORMAL: 0,
    PressureLevel.ELEVATED: 1,
    PressureLevel.HIGH: 2,
    PressureLevel.CRITICAL: 3,
}
SWAP_RATE_ELEVATED_RATIO_PER_MIN = 0.01
SWAP_RATE_HIGH_RATIO_PER_MIN = 0.03
SWAP_RATE_CRITICAL_RATIO_PER_MIN = 0.10


def max_pressure(*levels: PressureLevel) -> PressureLevel:
    return max(levels, key=lambda level: _PRESSURE_RANK[level])


def _classify_available(available_pct: float) -> PressureLevel:
    # Thresholds match the PressureLevel docstring; tuned for large workflow batch on
    # workstation. Common misunderstanding: 40% free is "fine, ramp up", not
    # "be cautious" — the 32 GB box is doing its job when memory is in active
    # use by agents + other apps.
    if available_pct > 25:
        return PressureLevel.NORMAL
    if available_pct > 15:
        return PressureLevel.ELEVATED
    if available_pct > 8:
        return PressureLevel.HIGH
    return PressureLevel.CRITICAL


def classify_swap_rate(
    swap_delta_gb_per_min: float,
    *,
    total_memory_gb: float,
) -> PressureLevel:
    """Classify active swap growth as pressure.

    Apple documents memory pressure as a composite of free memory, swap rate,
    wired memory, and cached memory. Absolute swap usage can remain high after
    pressure subsides, so the controller uses swap growth rate as the active
    pressure signal and logs absolute swap for visibility.
    """
    if total_memory_gb <= 0:
        raise ValueError("total_memory_gb must be > 0")
    if swap_delta_gb_per_min <= 0:
        return PressureLevel.NORMAL
    ratio_per_min = swap_delta_gb_per_min / total_memory_gb
    if ratio_per_min >= SWAP_RATE_CRITICAL_RATIO_PER_MIN:
        return PressureLevel.CRITICAL
    if ratio_per_min >= SWAP_RATE_HIGH_RATIO_PER_MIN:
        return PressureLevel.HIGH
    if ratio_per_min >= SWAP_RATE_ELEVATED_RATIO_PER_MIN:
        return PressureLevel.ELEVATED
    return PressureLevel.NORMAL


def _classify(
    available_pct: float,
    *,
    total_memory_gb: float,
    swap_delta_gb_per_min: float = 0.0,
) -> PressureLevel:
    return max_pressure(
        _classify_available(available_pct),
        classify_swap_rate(
            swap_delta_gb_per_min,
            total_memory_gb=total_memory_gb,
        ),
    )


# ── macOS ─────────────────────────────────────────────────────


def _read_command_stdout(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise UnsupportedTelemetryPlatformError(
            f"failed to read required telemetry: {' '.join(args)}"
        ) from exc
    return result.stdout.strip()


def _macos_total_memory_bytes() -> int:
    value = int(_read_command_stdout(["sysctl", "-n", "hw.memsize"]))
    if value <= 0:
        raise UnsupportedTelemetryPlatformError("macOS hw.memsize returned a non-positive value")
    return value


def _parse_macos_swap_used_gb(text: str) -> float:
    m = re.search(r"used\s*=\s*([\d.]+)M", text)
    if not m:
        raise UnsupportedTelemetryPlatformError(f"failed to parse macOS vm.swapusage: {text!r}")
    return float(m.group(1)) / 1024


def _macos_swap_used_gb() -> float:
    return _parse_macos_swap_used_gb(_read_command_stdout(["sysctl", "-n", "vm.swapusage"]))


def _measure_macos() -> MemoryPressure:
    level = float(_read_command_stdout(["sysctl", "-n", "kern.memorystatus_level"]))
    if not 0 <= level <= 100:
        raise UnsupportedTelemetryPlatformError(
            f"macOS kern.memorystatus_level out of range: {level}"
        )

    swap_gb = _macos_swap_used_gb()
    total_bytes = _macos_total_memory_bytes()
    total_gb = total_bytes / (1024**3)

    return MemoryPressure(
        available_pct=level,
        swap_used_gb=swap_gb,
        total_memory_gb=total_gb,
        level=_classify(level, total_memory_gb=total_gb),
        source="macos-memorystatus",
    )


# ── Linux ─────────────────────────────────────────────────────


def _measure_linux() -> MemoryPressure:
    meminfo_path = Path("/proc/meminfo")
    source = "linux-meminfo"

    try:
        text = meminfo_path.read_text()
    except OSError as exc:
        raise UnsupportedTelemetryPlatformError(
            "failed to read required Linux telemetry: /proc/meminfo"
        ) from exc

    total_m = re.search(r"MemTotal:\s+(\d+)", text)
    avail_m = re.search(r"MemAvailable:\s+(\d+)", text)
    swap_total_m = re.search(r"SwapTotal:\s+(\d+)", text)
    swap_free_m = re.search(r"SwapFree:\s+(\d+)", text)
    if not total_m or not avail_m or not swap_total_m or not swap_free_m:
        message = (
            "Linux /proc/meminfo missing required MemTotal, MemAvailable, "
            "SwapTotal, or SwapFree fields"
        )
        raise UnsupportedTelemetryPlatformError(message)

    total_kb = int(total_m.group(1))
    avail_kb = int(avail_m.group(1))
    swap_total_kb = int(swap_total_m.group(1))
    swap_free_kb = int(swap_free_m.group(1))
    if total_kb <= 0 or avail_kb < 0 or swap_total_kb < 0 or swap_free_kb < 0:
        raise UnsupportedTelemetryPlatformError(
            "Linux /proc/meminfo returned invalid memory counters"
        )

    available_pct = avail_kb / total_kb * 100
    swap_gb = (swap_total_kb - swap_free_kb) / (1024 * 1024)

    # Try PSI for a more precise signal (kernel 4.20+)
    psi_path = Path("/proc/pressure/memory")
    if psi_path.exists():
        try:
            text = psi_path.read_text()
            # "some avg10=X.XX avg60=X.XX avg300=X.XX total=XXXX"
            m = re.search(r"some avg10=([\d.]+)", text)
            if m:
                psi_some_avg10 = float(m.group(1))
                # PSI is % of time stalled on memory — invert to get "available"
                # PSI 0 = no pressure → available_pct stays high
                # PSI 50 = heavy pressure → available_pct should be low
                # Use PSI as a refinement: if PSI shows stalling, trust it over meminfo
                if psi_some_avg10 > 5:
                    psi_available = max(0, 100 - psi_some_avg10 * 2)
                    if psi_available < available_pct:
                        available_pct = psi_available
                        source = "linux-psi+meminfo"
        except OSError:
            pass

    total_gb = total_kb / (1024 * 1024)

    return MemoryPressure(
        available_pct=available_pct,
        swap_used_gb=swap_gb,
        total_memory_gb=total_gb,
        level=_classify(available_pct, total_memory_gb=total_gb),
        source=source,
    )


# ── Public API ────────────────────────────────────────────────


def available_memory_bytes() -> int:
    """Return current free/available memory in bytes.

    macOS: total × memorystatus_level%.
    Linux: MemAvailable from /proc/meminfo.
    """
    pressure = measure()
    return available_memory_bytes_from_pressure(pressure)


def available_memory_bytes_from_pressure(pressure: MemoryPressure) -> int:
    """Return available memory bytes from an existing pressure sample."""
    return int(pressure.total_memory_gb * (1024**3) * pressure.available_pct / 100)


def estimate_initial_concurrency(
    max_concurrency: int,
    per_process_bytes: int,
    budget_fraction: float = 0.5,
    pressure: MemoryPressure | None = None,
) -> int:
    """Estimate a safe initial concurrency from current free memory.

    Uses ``budget_fraction`` of free memory as the budget for initial processes
    — the remainder is headroom for the OS, existing processes, and ramp-up
    (the adaptive pool will continue adding slots if pressure stays normal).

    Returns min(budget / per_process_bytes, max_concurrency), floored at 1.
    """
    if budget_fraction <= 0:
        raise ValueError(f"budget_fraction must be > 0, got {budget_fraction}")
    free = (
        available_memory_bytes_from_pressure(pressure)
        if pressure is not None
        else available_memory_bytes()
    )
    budget = int(free * budget_fraction)
    initial = max(1, budget // per_process_bytes)
    result = min(initial, max_concurrency)
    message = (
        "Estimated initial concurrency: %d "
        "(free=%d MB, budget=%d MB, budget_fraction=%.2f, per_process=%d MB, max=%d)"
    )
    log.info(
        message,
        result,
        free // (1024 * 1024),
        budget // (1024 * 1024),
        budget_fraction,
        per_process_bytes // (1024 * 1024),
        max_concurrency,
    )
    return result


def adapt_batch_size(configured: int, level: PressureLevel) -> int:
    """Return an effective batch_size given current memory pressure.

    NORMAL/ELEVATED → keep original.
    HIGH → halve.
    CRITICAL → quarter.
    Never returns less than 1.
    """
    if level == PressureLevel.HIGH:
        return max(1, configured // 2)
    if level == PressureLevel.CRITICAL:
        return max(1, configured // 4)
    return configured


def measure() -> MemoryPressure:
    """Measure current memory pressure on supported macOS and Linux hosts."""
    system = platform.system()
    if system == "Darwin":
        return _measure_macos()
    if system == "Linux":
        return _measure_linux()
    message = (
        f"unsupported platform for memory pressure telemetry: {system}. "
        "Metaproc runpool currently supports recent macOS and modern Linux. "
        "Windows support is not implemented yet."
    )
    raise UnsupportedTelemetryPlatformError(message)


def _platform_label() -> str:
    """Return a compact platform label for telemetry-support errors."""
    system = platform.system()
    if system == "Darwin":
        version = platform.mac_ver()[0] or platform.release()
        return f"macOS {version}"
    if system == "Linux":
        return f"Linux {platform.release()}"
    return platform.platform() or system


def validate_supported_platform() -> MemoryPressure:
    """Probe the exact telemetry counters required by the runpool.

    Supported hosts must provide:

    - macOS: ``hw.memsize``, ``kern.memorystatus_level``, and ``vm.swapusage`` via
      ``sysctl``
    - Linux: ``MemTotal``, ``MemAvailable``, ``SwapTotal``, and ``SwapFree`` via
      ``/proc/meminfo``

    Returns the initial pressure reading so callers do not need a second probe.
    """
    try:
        return measure()
    except UnsupportedTelemetryPlatformError as exc:
        raise UnsupportedTelemetryPlatformError(
            f"unsupported runpool resource telemetry on {_platform_label()}: {exc}"
        ) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise UnsupportedTelemetryPlatformError(
            f"runpool cannot read required resource telemetry on {_platform_label()}: {exc}"
        ) from exc
