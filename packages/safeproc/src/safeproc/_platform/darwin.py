"""macOS provider, ported from the memory guard's fork-free readings.

Provenance: the ``ctypes`` structures, the ``host_statistics64`` and ``proc_pid_rusage``
paths, the ``vm.swapusage`` and swap-volume readings, the pressure alarm, and the
scheduling hardening are adapted from ``memory_guard.py`` (gist ``d5e67ea``), where each
was measured against the helper commands it replaces: ``host_statistics64`` in 12 µs
against a ``vm_stat`` fork, ``proc_pid_rusage`` in 9 µs per PID against a batched
``footprint`` at 313 ms, cross-checked to 0.015 percent.

Each native path keeps the guard's subprocess fallback, because a provider that fails
closed on an ABI change is worse than a slow one. The process table adds a ``libproc``
path (``proc_listallpids`` and ``proc_pidinfo``) that the guard did not have; it falls
back to the guard's ``ps`` reading when libproc does not answer.

HANDOFF: this module was written on Linux and has not run on macOS. The native macOS
agent must validate every ``ctypes`` layout and the ``proc_bsdinfo`` field offsets
against the SDK headers before trusting a reading; ``docs/architecture.md`` lists the
checks.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from safeproc._platform.base import Capabilities
from safeproc.identity import ProcessRecord
from safeproc.models import ALARM_NORMAL, HostSample, PlatformName

_HOST_VM_INFO64 = 4
_RUSAGE_INFO_V4 = 4
_PROC_PIDTBSDINFO = 3
_PROC_ALL_PIDS = 1
_SZOMB = 5
_MB = 1048576
_GB = 1e9


class _VMStatistics64(ctypes.Structure):
    """``vm_statistics64`` from <mach/vm_statistics.h>. Field order is the ABI."""

    _fields_ = (
        [(n, ctypes.c_uint32) for n in ("free", "active", "inactive", "wire")]
        + [
            (n, ctypes.c_uint64)
            for n in (
                "zero_fill",
                "reactivations",
                "pageins",
                "pageouts",
                "faults",
                "cow_faults",
                "lookups",
                "hits",
                "purges",
            )
        ]
        + [("purgeable", ctypes.c_uint32), ("speculative", ctypes.c_uint32)]
        + [(n, ctypes.c_uint64) for n in ("decompressions", "compressions", "swapins", "swapouts")]
        + [
            ("compressor_page_count", ctypes.c_uint32),
            ("throttled", ctypes.c_uint32),
            ("external_page_count", ctypes.c_uint32),
            ("internal_page_count", ctypes.c_uint32),
            ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
        ]
    )


class _RUsageInfoV4(ctypes.Structure):
    """``rusage_info_v4`` from <libproc.h>. Footprint and resident size are read."""

    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (n, ctypes.c_uint64)
        for n in (
            "ri_user_time",
            "ri_system_time",
            "ri_pkg_idle_wkups",
            "ri_interrupt_wkups",
            "ri_pageins",
            "ri_wired_size",
            "ri_resident_size",
            "ri_phys_footprint",
            "ri_proc_start_abstime",
            "ri_proc_exit_abstime",
            "ri_child_user_time",
            "ri_child_system_time",
            "ri_child_pkg_idle_wkups",
            "ri_child_interrupt_wkups",
            "ri_child_pageins",
            "ri_child_elapsed_abstime",
            "ri_diskio_bytesread",
            "ri_diskio_byteswritten",
            "ri_cpu_time_qos_default",
            "ri_cpu_time_qos_maintenance",
            "ri_cpu_time_qos_background",
            "ri_cpu_time_qos_utility",
            "ri_cpu_time_qos_legacy",
            "ri_cpu_time_qos_user_initiated",
            "ri_cpu_time_qos_user_interactive",
            "ri_billed_system_time",
            "ri_serviced_system_time",
            "ri_logical_writes",
            "ri_lifetime_max_phys_footprint",
            "ri_instructions",
            "ri_cycles",
            "ri_billed_energy",
            "ri_serviced_energy",
            "ri_interval_max_phys_footprint",
            "ri_runnable_time",
        )
    ]


class _XswUsage(ctypes.Structure):
    """``struct xsw_usage`` from <sys/sysctl.h>."""

    _fields_ = [
        ("total", ctypes.c_uint64),
        ("avail", ctypes.c_uint64),
        ("used", ctypes.c_uint64),
        ("pagesize", ctypes.c_int32),
        ("encrypted", ctypes.c_bool),
    ]


class _ProcBsdInfo(ctypes.Structure):
    """``struct proc_bsdinfo`` from <sys/proc_info.h>; 136 bytes. HANDOFF: verify offsets."""

    _fields_ = (
        [
            (n, ctypes.c_uint32)
            for n in (
                "pbi_flags",
                "pbi_status",
                "pbi_xstatus",
                "pbi_pid",
                "pbi_ppid",
                "pbi_uid",
                "pbi_gid",
                "pbi_ruid",
                "pbi_rgid",
                "pbi_svuid",
                "pbi_svgid",
                "rfu_1",
            )
        ]
        + [("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32)]
        + [
            (n, ctypes.c_uint32)
            for n in ("pbi_nfiles", "pbi_pgid", "pbi_pjobc", "e_tdev", "e_tpgid")
        ]
        + [("pbi_nice", ctypes.c_int32)]
        + [("pbi_start_tvsec", ctypes.c_uint64), ("pbi_start_tvusec", ctypes.c_uint64)]
    )


class LibSystem(NamedTuple):
    libc: ctypes.CDLL
    host_port: int
    page_size: int


def _load_libsystem() -> LibSystem | None:
    """Bind libSystem for the hot path, or ``None`` to fall back to subprocesses."""
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        libc.mach_host_self.restype = ctypes.c_uint
        libc.host_statistics64.argtypes = [
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint),
        ]
        libc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_RUsageInfoV4)]
        libc.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        libc.proc_listpids.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
        libc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        page = ctypes.c_uint64()
        size = ctypes.c_size_t(ctypes.sizeof(page))
        if libc.sysctlbyname(b"hw.pagesize", ctypes.byref(page), ctypes.byref(size), None, 0) != 0:
            return None
        return LibSystem(libc, int(libc.mach_host_self()), int(page.value))
    except (OSError, AttributeError):
        return None


def _run(*argv: str) -> str:
    """One subprocess's stdout, or empty on any failure. Gauges degrade; they do not raise."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class HostMemory(NamedTuple):
    free_gb: float
    reclaimable_gb: float
    wired_gb: float
    compressed_gb: float
    ancm_ratio: float
    swapins: float
    swapouts: float


class SwapUsage(NamedTuple):
    total_mb: float
    used_mb: float


_ETIME = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")


def parse_etime(text: str) -> int:
    """``[[dd-]hh:]mm:ss`` to seconds; macOS ``ps`` has no ``etimes`` keyword."""
    match = _ETIME.match(text.strip())
    if not match:
        return 0
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_ps_table(text: str, now_epoch: float) -> list[ProcessRecord]:
    """The guard's ``ps -eo pid=,ppid=,stat=,uid=,rss=,etime=,args=`` reading.

    The creation token is the start second derived from elapsed time, which is coarse;
    the libproc path supplies microseconds. Zombies are kept in the table with state
    ``Z`` so callers can exclude them uniformly.
    """
    rows: list[ProcessRecord] = []
    for line in text.splitlines():
        parts = line.split(maxsplit=6)
        if len(parts) < 7:
            continue
        try:
            age = parse_etime(parts[5])
            rows.append(
                ProcessRecord(
                    pid=int(parts[0]),
                    ppid=int(parts[1]),
                    uid=int(parts[3]),
                    state=parts[2],
                    rss_mb=int(parts[4]) / 1024,
                    age_s=float(age),
                    cmd=parts[6],
                    create_token=int(now_epoch - age),
                )
            )
        except ValueError:
            continue
    return rows


@dataclass
class DarwinProvider:
    """Darwin evidence through libSystem, with the guard's helper-command fallbacks."""

    def __init__(self) -> None:
        self._lib = _load_libsystem()
        self._swap_volume = self._detect_swap_volume()
        self._capabilities = self._build_capabilities()

    # ── capabilities ─────────────────────────────────────────────────────────

    def _build_capabilities(self) -> Capabilities:
        native = self._lib is not None
        notes = (
            "ported from memory_guard.py; not yet validated natively on macOS",
            "no kernel safety net: CONFIG_JETSAM is not compiled into macOS",
        )
        return Capabilities(
            platform=PlatformName.DARWIN,
            host_budget="host_statistics64 free + inactive + purgeable pages"
            if native
            else "vm_stat free + inactive + purgeable pages (helper)",
            alarm="kern.memorystatus_vm_pressure_level (1/2/4)",
            process_cost="proc_pid_rusage ri_phys_footprint" if native else "footprint(1) (helper)",
            degradation="compressor growth, reclaimable slope, swap deltas, suspension distance, red-line ratio",
            psi="n/a",
            cgroup_headroom=False,
            swap_volume=self._swap_volume,
            sleep_clock="CLOCK_MONOTONIC (continues across sleep on Darwin)",
            identity="pid plus proc_bsdinfo start time" if native else "pid plus ps start second",
            sampling="native" if native else "helper",
            notes=notes,
        )

    def capabilities(self) -> Capabilities:
        return self._capabilities

    # ── sysctl helpers ───────────────────────────────────────────────────────

    def _sysctl_u64(self, name: bytes) -> int | None:
        if self._lib is None:
            return None
        value = ctypes.c_uint64()
        size = ctypes.c_size_t(ctypes.sizeof(value))
        rc = self._lib.libc.sysctlbyname(name, ctypes.byref(value), ctypes.byref(size), None, 0)
        return int(value.value) if rc == 0 else None

    def _sysctl_int(self, name: bytes) -> int | None:
        if self._lib is None:
            return None
        value = ctypes.c_int32()
        size = ctypes.c_size_t(ctypes.sizeof(value))
        rc = self._lib.libc.sysctlbyname(name, ctypes.byref(value), ctypes.byref(size), None, 0)
        return int(value.value) if rc == 0 else None

    def _sysctl_str(self, name: bytes) -> str | None:
        if self._lib is None:
            return None
        buffer = ctypes.create_string_buffer(1024)
        size = ctypes.c_size_t(ctypes.sizeof(buffer))
        rc = self._lib.libc.sysctlbyname(name, buffer, ctypes.byref(size), None, 0)
        return buffer.value.decode("utf-8", "replace") if rc == 0 else None

    # ── host ─────────────────────────────────────────────────────────────────

    def host_memory(self) -> HostMemory:
        """VM counters via ``host_statistics64`` when available, else ``vm_stat``."""
        if self._lib is not None:
            libc, host, page = self._lib
            stats = _VMStatistics64()
            count = ctypes.c_uint(ctypes.sizeof(stats) // 4)
            if (
                libc.host_statistics64(
                    host, _HOST_VM_INFO64, ctypes.byref(stats), ctypes.byref(count)
                )
                == 0
            ):
                ancm = stats.free + stats.active + stats.inactive + stats.speculative
                return HostMemory(
                    free_gb=stats.free * page / _GB,
                    reclaimable_gb=(stats.free + stats.inactive + stats.purgeable) * page / _GB,
                    wired_gb=stats.wire * page / _GB,
                    compressed_gb=stats.compressor_page_count * page / _GB,
                    ancm_ratio=ancm / max(1, ancm + stats.compressor_page_count),
                    swapins=float(stats.swapins),
                    swapouts=float(stats.swapouts),
                )
        out = _run("vm_stat")
        match = re.search(r"page size of (\d+)", out)
        page_size = int(match.group(1)) if match else 16384

        def pages(label: str) -> int:
            found = re.search(rf"Pages {label}:\s+(\d+)", out)
            return int(found.group(1)) if found else 0

        def counter(label: str) -> float:
            found = re.search(rf"{label}:\s+(\d+)", out)
            return float(found.group(1)) if found else 0.0

        ancm = pages("free") + pages("active") + pages("inactive") + pages("speculative")
        compressor = pages("occupied by compressor")
        return HostMemory(
            free_gb=pages("free") * page_size / _GB,
            reclaimable_gb=(pages("free") + pages("inactive") + pages("purgeable"))
            * page_size
            / _GB,
            wired_gb=pages("wired down") * page_size / _GB,
            compressed_gb=compressor * page_size / _GB,
            ancm_ratio=ancm / max(1, ancm + compressor),
            swapins=counter("Swapins"),
            swapouts=counter("Swapouts"),
        )

    def swap_usage(self) -> SwapUsage:
        """``vm.swapusage`` via sysctlbyname, without forking; falls back to the CLI.

        The CLI prints ``total = N.NNM  used = N.NNM  free = N.NNM``, so the values sit at
        whitespace indices 2, 5, and 8; index 6 is the word ``free`` and parses to zero,
        which is how a sibling gauge measured nothing for months.
        """
        if self._lib is not None:
            usage = _XswUsage()
            size = ctypes.c_size_t(ctypes.sizeof(usage))
            rc = self._lib.libc.sysctlbyname(
                b"vm.swapusage", ctypes.byref(usage), ctypes.byref(size), None, 0
            )
            if rc == 0:
                return SwapUsage(usage.total / _MB, usage.used / _MB)
        parts = _run("sysctl", "-n", "vm.swapusage").split()

        def field(index: int) -> float:
            try:
                return float(parts[index].rstrip("M"))
            except (IndexError, ValueError):
                return 0.0

        return SwapUsage(field(2), field(5))

    def _detect_swap_volume(self) -> str:
        """The mount point swapfiles are written to, from ``vm.swapfileprefix``."""
        prefix = self._sysctl_str(b"vm.swapfileprefix")
        if prefix is None:
            prefix = _run("sysctl", "-n", "vm.swapfileprefix").strip()
        path = Path(prefix).parent if prefix else Path("/")
        if not path.is_dir():
            path = Path("/")
        try:
            while str(path) != "/" and not path.is_mount():
                path = path.parent
        except OSError:
            return "/"
        return str(path)

    def disk_avail_gb(self) -> float:
        """Free space on the swap volume in GB; ``f_bavail`` bounds a new swapfile."""
        try:
            stat = os.statvfs(self._swap_volume)
        except OSError:
            try:
                stat = os.statvfs("/")
            except OSError:
                return 999.0
        return stat.f_bavail * stat.f_frsize / _GB

    def pressure_level(self) -> int:
        """``kern.memorystatus_vm_pressure_level``: 1 normal, 2 warning, 4 critical."""
        level = self._sysctl_int(b"kern.memorystatus_vm_pressure_level")
        if level is not None:
            return level
        out = _run("sysctl", "-n", "kern.memorystatus_vm_pressure_level").strip()
        return int(out) if out.isdigit() else ALARM_NORMAL

    def host_sample(self) -> HostSample:
        memory = self.host_memory()
        swap = self.swap_usage()
        memsize = self._sysctl_u64(b"hw.memsize")
        return HostSample(
            platform=PlatformName.DARWIN,
            reclaimable_gb=memory.reclaimable_gb,
            free_gb=memory.free_gb,
            pressure=self.pressure_level(),
            wired_gb=memory.wired_gb,
            compressed_gb=memory.compressed_gb,
            swap_used_mb=swap.used_mb,
            swap_total_mb=swap.total_mb,
            disk_gb=self.disk_avail_gb(),
            ancm_ratio=memory.ancm_ratio,
            total_gb=(memsize / _GB) if memsize else None,
        )

    # ── processes ────────────────────────────────────────────────────────────

    def _native_table(self) -> list[ProcessRecord] | None:
        if self._lib is None:
            return None
        libc = self._lib.libc
        needed = libc.proc_listpids(_PROC_ALL_PIDS, 0, None, 0)
        if needed <= 0:
            return None
        count = int(needed) // ctypes.sizeof(ctypes.c_int) + 64
        pids = (ctypes.c_int * count)()
        got = libc.proc_listpids(_PROC_ALL_PIDS, 0, pids, ctypes.sizeof(pids))
        if got <= 0:
            return None
        now = time.time()
        rows: list[ProcessRecord] = []
        for index in range(int(got) // ctypes.sizeof(ctypes.c_int)):
            pid = int(pids[index])
            if pid <= 0:
                continue
            info = _ProcBsdInfo()
            size = libc.proc_pidinfo(
                pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)
            )
            if size < ctypes.sizeof(info):
                continue
            usage = _RUsageInfoV4()
            rss_mb = 0.0
            if libc.proc_pid_rusage(pid, _RUSAGE_INFO_V4, ctypes.byref(usage)) == 0:
                rss_mb = usage.ri_resident_size / _MB
            path = ctypes.create_string_buffer(4096)
            cmd = ""
            if libc.proc_pidpath(pid, path, ctypes.sizeof(path)) > 0:
                cmd = path.value.decode("utf-8", "replace")
            if not cmd:
                cmd = f"[{info.pbi_comm.decode('utf-8', 'replace')}]"
            start = float(info.pbi_start_tvsec) + float(info.pbi_start_tvusec) / 1e6
            rows.append(
                ProcessRecord(
                    pid=pid,
                    ppid=int(info.pbi_ppid),
                    uid=int(info.pbi_uid),
                    state="Z" if int(info.pbi_status) == _SZOMB else "R",
                    rss_mb=rss_mb,
                    age_s=max(0.0, now - start),
                    cmd=cmd,
                    create_token=int(info.pbi_start_tvsec) * 1_000_000 + int(info.pbi_start_tvusec),
                )
            )
        return rows

    def process_table(self) -> list[ProcessRecord]:
        native = self._native_table()
        if native:
            return native
        return parse_ps_table(
            _run("ps", "-eo", "pid=,ppid=,stat=,uid=,rss=,etime=,args="), time.time()
        )

    def discovery_table(self) -> list[ProcessRecord]:
        """The ``ps`` reading with full argv, for one-off pattern discovery only.

        ``proc_pidpath`` returns the executable, not the arguments, so pattern matching
        against argv needs this helper. It is never on the sampling path.
        """
        return parse_ps_table(
            _run("ps", "-eo", "pid=,ppid=,stat=,uid=,rss=,etime=,args="), time.time()
        )

    def footprint_mb(self, pid: int) -> float | None:
        if self._lib is None:
            return None
        usage = _RUsageInfoV4()
        if self._lib.libc.proc_pid_rusage(pid, _RUSAGE_INFO_V4, ctypes.byref(usage)) != 0:
            return None
        return usage.ri_phys_footprint / _MB

    _FOOTPRINT_LINE = re.compile(r"^(.+?) \[(\d+)\].*?Footprint:\s+(\d+)\s*B")

    def costs(self, pids: Sequence[int], min_mb: float) -> dict[int, float]:
        if not pids:
            return {}
        if self._lib is not None:
            measured: dict[int, float] = {}
            for pid in pids:
                mb = self.footprint_mb(pid)
                if mb is not None and mb >= min_mb:
                    measured[pid] = mb
            return measured
        out = _run(
            "footprint",
            "--noCategories",
            "-f",
            "bytes",
            "--minFootprint",
            str(int(max(1, min_mb))),
            *(str(p) for p in pids),
        )
        found: dict[int, float] = {}
        for line in out.splitlines():
            match = self._FOOTPRINT_LINE.match(line)
            if match:
                found[int(match.group(2))] = int(match.group(3)) / _MB
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
        if self._lib is not None:
            info = _ProcBsdInfo()
            size = self._lib.libc.proc_pidinfo(
                pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)
            )
            if size >= ctypes.sizeof(info):
                return int(info.pbi_status) != _SZOMB
            return False
        live = {row.pid for row in self.process_table() if not row.is_zombie}
        return pid in live

    def current_uid(self) -> int:
        return os.getuid()

    def machine_facts(self) -> Mapping[str, object]:
        memsize = self._sysctl_u64(b"hw.memsize")
        if memsize is None:
            text = _run("sysctl", "-n", "hw.memsize").strip()
            memsize = int(text) if text.isdigit() else None
        swap = self.swap_usage()
        return {
            "host": platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "os_product": _run("sw_vers", "-productVersion").strip(),
            "arch": platform.machine(),
            "cpus_logical": os.cpu_count(),
            "ram_gb": round(memsize / _GB, 1) if memsize else None,
            "swap_total_mb": round(swap.total_mb, 1),
            "swap_volume": self._swap_volume,
            "disk_free_gb": round(self.disk_avail_gb(), 2),
            "native": self._lib is not None,
        }

    def harden_scheduling(self) -> str:
        """Raise this thread to ``MAXPRI_USER`` (63) with the two thread policies.

        Not QoS, which a per-task ceiling clamps to a silent no-op, and not
        ``setpriority``, which cannot be undone without root. This buys run-queue
        position, not page waits: the free-page wait queue is FIFO below real-time
        priority, so a sentinel blocked on pages is helped only by not needing them.
        """
        if self._lib is None:
            return "unavailable"
        libc = self._lib.libc
        try:
            libc.mach_thread_self.restype = ctypes.c_uint
            libc.mach_task_self.restype = ctypes.c_uint
            libc.mach_port_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint]
            libc.thread_policy_set.argtypes = [
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint,
            ]

            class _Precedence(ctypes.Structure):
                _fields_ = [("importance", ctypes.c_int)]

            class _Extended(ctypes.Structure):
                _fields_ = [("timeshare", ctypes.c_int)]

            thread = libc.mach_thread_self()
            try:
                precedence = _Precedence(63)
                rc1 = libc.thread_policy_set(thread, 3, ctypes.byref(precedence), 1)
                extended = _Extended(0)
                rc2 = libc.thread_policy_set(thread, 1, ctypes.byref(extended), 1)
            finally:
                libc.mach_port_deallocate(libc.mach_task_self(), thread)
        except (OSError, AttributeError):
            return "unavailable"
        return "hardened" if rc1 == 0 and rc2 == 0 else f"partial(rc={rc1},{rc2})"
