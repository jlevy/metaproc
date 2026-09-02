"""Linux provider: procfs and cgroup v2, no helper commands.

Budget is ``MemAvailable`` bounded by the caller's own cgroup headroom, because inside a
memory-limited cgroup the host figure can exceed what the cgroup may use by an order of
magnitude. Pressure Stall Information is a three-state capability, absent, averages, or
triggers, and the cgroup-local ``memory.pressure`` is preferred when it exists. There is
no compressor to watch; the degradation signals beside stall time are ``MemAvailable``
slope and swap-in rate. Process cost is proportional set size from ``smaps_rollup``,
which walks page tables and is therefore read only behind the engine's accuracy gate.

The normalized 1/2/4 alarm is derived from stall time when PSI exists and from the
available fraction otherwise. Those thresholds are design choices awaiting calibration
on a dedicated host (bead ``mp-sfc0``); they are configurable and journaled.
"""

from __future__ import annotations

import os
import platform
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from safeproc._platform.base import Capabilities
from safeproc.identity import ProcessRecord
from safeproc.models import ALARM_CRITICAL, ALARM_NORMAL, ALARM_WARNING, HostSample, PlatformName

_GB = 1e9
_KB = 1024.0


@dataclass(frozen=True)
class LinuxAlarmConfig:
    """How stall time and available fraction map to the 1/2/4 alarm. Uncalibrated."""

    warning_some_pct: float = 2.0
    """``some`` stall over ten seconds that reads as warning."""

    critical_full_pct: float = 10.0
    """``full`` stall over ten seconds that reads as critical."""

    warning_available_fraction: float = 0.15
    """Available fraction of the budget below which the host reads as warning."""

    critical_available_fraction: float = 0.08
    """Available fraction of the budget below which the host reads as critical."""


def derive_alarm(
    *,
    available_fraction: float,
    stall_some_pct: float | None,
    stall_full_pct: float | None,
    config: LinuxAlarmConfig,
) -> int:
    """Normalize Linux evidence to the macOS-shaped alarm the policy consumes."""
    level = ALARM_NORMAL
    if available_fraction < config.critical_available_fraction:
        level = ALARM_CRITICAL
    elif available_fraction < config.warning_available_fraction:
        level = ALARM_WARNING
    if stall_full_pct is not None and stall_full_pct >= config.critical_full_pct:
        level = max(level, ALARM_CRITICAL)
    elif stall_some_pct is not None and stall_some_pct >= config.warning_some_pct:
        level = max(level, ALARM_WARNING)
    return level


_MEMINFO_KEYS = ("MemTotal", "MemFree", "MemAvailable", "SwapTotal", "SwapFree", "Zswapped")


def parse_meminfo(text: str) -> dict[str, int]:
    """The fields the provider needs from ``/proc/meminfo``, in kB."""
    found: dict[str, int] = {}
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        if not sep or key not in _MEMINFO_KEYS:
            continue
        digits = rest.strip().split()
        if digits and digits[0].isdigit():
            found[key] = int(digits[0])
    return found


_PSI_LINE = re.compile(r"^(some|full)\s+avg10=([\d.]+)")


def parse_psi(text: str) -> tuple[float | None, float | None]:
    """``some`` and ``full`` ten-second averages from a PSI file."""
    some: float | None = None
    full: float | None = None
    for line in text.splitlines():
        match = _PSI_LINE.match(line.strip())
        if not match:
            continue
        value = float(match.group(2))
        if match.group(1) == "some":
            some = value
        else:
            full = value
    return some, full


def parse_stat(text: str) -> tuple[str, str, int, int, int] | None:
    """``(comm, state, ppid, starttime_ticks, rss_pages)`` from ``/proc/<pid>/stat``.

    The command name is parenthesized and may contain spaces, so the line is split at the
    last closing parenthesis rather than on whitespace.
    """
    open_paren = text.find("(")
    close_paren = text.rfind(")")
    if open_paren < 0 or close_paren < 0:
        return None
    comm = text[open_paren + 1 : close_paren]
    fields = text[close_paren + 2 :].split()
    # After ")": state is overall field 3, ppid 4, starttime 22, rss 24.
    if len(fields) < 22:
        return None
    try:
        return comm, fields[0], int(fields[1]), int(fields[19]), int(fields[21])
    except ValueError:
        return None


def parse_cgroup_path(text: str) -> str | None:
    """The unified-hierarchy path from ``/proc/self/cgroup``, or ``None`` on v1-only hosts."""
    for line in text.splitlines():
        parts = line.strip().split(":", 2)
        if len(parts) == 3 and parts[0] == "0" and parts[1] == "":
            return parts[2]
    return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


class LinuxProvider:
    """procfs-backed evidence. Every read is a file; nothing is forked."""

    def __init__(
        self,
        proc: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        *,
        alarm: LinuxAlarmConfig | None = None,
    ) -> None:
        self._proc = proc
        self._cgroup_root = cgroup_root
        self._alarm = alarm or LinuxAlarmConfig()
        self._clk_tck = float(os.sysconf("SC_CLK_TCK")) if hasattr(os, "sysconf") else 100.0
        self._page = float(os.sysconf("SC_PAGE_SIZE")) if hasattr(os, "sysconf") else 4096.0
        self._own_cgroup = self._detect_cgroup()
        self._psi_path = self._detect_psi()
        self._last_swapin: tuple[float, int] | None = None
        self._capabilities = self._build_capabilities()

    # ── capabilities ─────────────────────────────────────────────────────────

    def _detect_cgroup(self) -> Path | None:
        text = _read_text(self._proc / "self" / "cgroup")
        if text is None or not (self._cgroup_root / "cgroup.controllers").exists():
            return None
        rel = parse_cgroup_path(text)
        if rel is None:
            return None
        path = self._cgroup_root / rel.lstrip("/")
        return path if (path / "memory.current").exists() else None

    def _detect_psi(self) -> Path | None:
        if self._own_cgroup is not None and (self._own_cgroup / "memory.pressure").exists():
            return self._own_cgroup / "memory.pressure"
        host = self._proc / "pressure" / "memory"
        return host if host.exists() else None

    def _build_capabilities(self) -> Capabilities:
        psi_state = "absent"
        if self._psi_path is not None:
            psi_state = "averages"
        budget = "/proc/meminfo MemAvailable"
        if self._own_cgroup is not None:
            budget += " bounded by own cgroup memory.max minus memory.current"
        notes = (
            "alarm thresholds are uncalibrated placeholders pending mp-sfc0",
            "PSS via smaps_rollup walks page tables; read only behind the accuracy gate",
            "no compressor: degradation is stall time and swap-in rate",
        )
        return Capabilities(
            platform=PlatformName.LINUX,
            host_budget=budget,
            alarm="derived from PSI stall averages and available fraction",
            process_cost="smaps_rollup Pss",
            degradation="PSI some/full avg10, MemAvailable slope, /proc/vmstat pswpin rate",
            psi=psi_state,
            cgroup_headroom=self._own_cgroup is not None,
            swap_volume="n/a",
            sleep_clock="CLOCK_BOOTTIME",
            identity="pid plus /proc/<pid>/stat starttime",
            sampling="native",
            notes=notes,
        )

    def capabilities(self) -> Capabilities:
        return self._capabilities

    # ── host ─────────────────────────────────────────────────────────────────

    def _cgroup_headroom_bytes(self) -> int | None:
        if self._own_cgroup is None:
            return None
        limit_text = _read_text(self._own_cgroup / "memory.max")
        current_text = _read_text(self._own_cgroup / "memory.current")
        if limit_text is None or current_text is None:
            return None
        limit_text = limit_text.strip()
        if limit_text == "max" or not limit_text.isdigit() or not current_text.strip().isdigit():
            return None
        return max(0, int(limit_text) - int(current_text.strip()))

    def _swapin_rate(self) -> float | None:
        text = _read_text(self._proc / "vmstat")
        if text is None:
            return None
        match = re.search(r"^pswpin\s+(\d+)$", text, re.MULTILINE)
        if not match:
            return None
        now = time.monotonic()
        count = int(match.group(1))
        previous = self._last_swapin
        self._last_swapin = (now, count)
        if previous is None or now <= previous[0]:
            return 0.0
        return max(0.0, (count - previous[1]) / (now - previous[0]))

    def host_sample(self) -> HostSample:
        text = _read_text(self._proc / "meminfo")
        if text is None:
            raise OSError("cannot read /proc/meminfo")
        info = parse_meminfo(text)
        total_kb = info.get("MemTotal", 0)
        avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
        free_kb = info.get("MemFree", 0)
        swap_total_kb = info.get("SwapTotal", 0)
        swap_free_kb = info.get("SwapFree", 0)
        zswapped_kb = info.get("Zswapped", 0)

        budget_bytes = avail_kb * _KB
        headroom = self._cgroup_headroom_bytes()
        cgroup_headroom_gb: float | None = None
        if headroom is not None:
            cgroup_headroom_gb = headroom / _GB
            budget_bytes = min(budget_bytes, float(headroom))

        some: float | None = None
        full: float | None = None
        if self._psi_path is not None:
            psi_text = _read_text(self._psi_path)
            if psi_text is not None:
                some, full = parse_psi(psi_text)

        denominator = total_kb * _KB
        if headroom is not None:
            limit_text = _read_text(self._own_cgroup / "memory.max") if self._own_cgroup else None
            if limit_text is not None and limit_text.strip().isdigit():
                denominator = min(denominator, float(limit_text.strip()))
        fraction = budget_bytes / denominator if denominator > 0 else 1.0

        return HostSample(
            platform=PlatformName.LINUX,
            reclaimable_gb=budget_bytes / _GB,
            free_gb=free_kb * _KB / _GB,
            pressure=derive_alarm(
                available_fraction=fraction,
                stall_some_pct=some,
                stall_full_pct=full,
                config=self._alarm,
            ),
            compressed_gb=zswapped_kb * _KB / _GB,
            swap_used_mb=(swap_total_kb - swap_free_kb) / _KB,
            swap_total_mb=swap_total_kb / _KB,
            total_gb=total_kb * _KB / _GB,
            cgroup_headroom_gb=cgroup_headroom_gb,
            stall_some_pct=some,
            stall_full_pct=full,
            swapin_rate_per_s=self._swapin_rate(),
        )

    # ── processes ────────────────────────────────────────────────────────────

    def _uptime(self) -> float:
        text = _read_text(self._proc / "uptime")
        if text is None:
            return 0.0
        try:
            return float(text.split()[0])
        except (IndexError, ValueError):
            return 0.0

    def process_table(self) -> list[ProcessRecord]:
        uptime = self._uptime()
        rows: list[ProcessRecord] = []
        try:
            entries = list(self._proc.iterdir())
        except OSError:
            return rows
        for pid_dir in entries:
            entry = pid_dir.name
            if not entry.isdigit():
                continue
            pid = int(entry)
            stat_text = _read_text(pid_dir / "stat")
            if stat_text is None:
                continue
            parsed = parse_stat(stat_text)
            if parsed is None:
                continue
            comm, state, ppid, start_ticks, rss_pages = parsed
            try:
                uid = pid_dir.stat().st_uid
            except OSError:
                continue
            cmd = self._cmdline(pid_dir) or f"[{comm}]"
            age = max(0.0, uptime - start_ticks / self._clk_tck)
            rows.append(
                ProcessRecord(
                    pid=pid,
                    ppid=ppid,
                    uid=uid,
                    state=state,
                    rss_mb=rss_pages * self._page / (1024 * 1024),
                    age_s=age,
                    cmd=cmd,
                    create_token=start_ticks,
                )
            )
        return rows

    def discovery_table(self) -> list[ProcessRecord]:
        return self.process_table()

    def _cmdline(self, pid_dir: Path) -> str:
        try:
            raw = (pid_dir / "cmdline").read_bytes()
        except OSError:
            return ""
        return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)

    def costs(self, pids: Sequence[int], min_mb: float) -> dict[int, float]:
        found: dict[int, float] = {}
        for pid in pids:
            text = _read_text(self._proc / str(pid) / "smaps_rollup")
            if text is None:
                continue
            match = re.search(r"^Pss:\s+(\d+)\s+kB", text, re.MULTILINE)
            if not match:
                continue
            mb = int(match.group(1)) / 1024
            if mb >= min_mb:
                found[pid] = mb
        return found

    def signal(self, pid: int, sig: int) -> bool:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return False
        except PermissionError:
            return False
        return True

    def alive(self, pid: int) -> bool:
        text = _read_text(self._proc / str(pid) / "stat")
        if text is None:
            return False
        parsed = parse_stat(text)
        return parsed is not None and not parsed[1].startswith("Z")

    def current_uid(self) -> int:
        return os.getuid()

    def machine_facts(self) -> Mapping[str, object]:
        info = parse_meminfo(_read_text(self._proc / "meminfo") or "")
        return {
            "host": platform.node(),
            "os": f"Linux {platform.release()}",
            "arch": platform.machine(),
            "cpus_logical": os.cpu_count(),
            "ram_gb": round(info.get("MemTotal", 0) * _KB / _GB, 1),
            "swap_total_mb": round(info.get("SwapTotal", 0) / _KB, 1),
            "cgroup": str(self._own_cgroup) if self._own_cgroup else None,
            "psi": self._capabilities.psi,
            "alarm_config": {
                "warning_some_pct": self._alarm.warning_some_pct,
                "critical_full_pct": self._alarm.critical_full_pct,
                "warning_available_fraction": self._alarm.warning_available_fraction,
                "critical_available_fraction": self._alarm.critical_available_fraction,
            },
        }

    def harden_scheduling(self) -> str:
        """No unprivileged priority raise exists on Linux; the sentinel relies on not forking."""
        return "unavailable"
