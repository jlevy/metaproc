"""Process resource context — VM + cgroup + OS + runtime limits.

Captures every level of resource limit that matters when debugging OOM or
CPU-starved agent processes:

- **VM level**: host CPU count + RAM from /proc/cpuinfo + /proc/meminfo.
- **Batch-task level**: container cgroup CPU + memory quotas (cgroup v1
  and v2). This is where the GCP Batch ``compute_resource.memory_mib``
  ceiling shows up; if it defaults to 2 GB the cgroup value reveals that.
- **OS level**: ``resource.getrlimit`` snapshot (NOFILE, NPROC, AS, DATA).
- **Runtime level**: Python version, sys.maxsize, environment variables
  that influence concurrency (``METAPROC_*``), Node heap (``NODE_OPTIONS``).

Read by ``log_resource_context`` at the top of the worker / orchestrator
container entrypoints and optionally by a ``metaproc resources`` CLI.

Per ``docs/project/development-rules.md`` § "Use the Existing Tools;
Don't Write One-Off Scripts": new diagnostic needs → new tool, not
another one-off script.
"""

from __future__ import annotations

import logging
import os
import resource
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Environment variables worth surfacing in the startup dump. Kept curated
# rather than "all env" so the dump stays readable and doesn't leak secrets.
_KNOWN_ENV_PREFIXES: tuple[str, ...] = ("METAPROC_", "BATCH_", "RUNS_DIR", "NODE_OPTIONS")
_REDACTED_TOKENS: tuple[str, ...] = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    # cgroup files may say "max" for unbounded.
    if text == "max":
        return -1
    try:
        return int(text.split()[0])
    except ValueError:
        return None


def _parse_cpu_max_v2(text: str) -> tuple[int | None, int]:
    """Parse cgroup v2 cpu.max content "QUOTA PERIOD" or "max PERIOD".

    Returns (quota_or_None_if_unlimited, period).
    """
    parts = text.strip().split()
    if len(parts) < 2:
        return None, 100000
    quota_str, period_str = parts[0], parts[1]
    try:
        period = int(period_str)
    except ValueError:
        period = 100000
    if quota_str == "max":
        return None, period
    try:
        return int(quota_str), period
    except ValueError:
        return None, period


@dataclass(frozen=True)
class HostInfo:
    """Host-visible CPU + memory — equals the VM shape on dedicated-VM runs."""

    cpu_count: int | None
    memory_total_mib: int | None
    memory_available_mib: int | None


@dataclass(frozen=True)
class CgroupLimits:
    """Cgroup-enforced limits on the calling process.

    Populated from cgroup v2 (``/sys/fs/cgroup/{cpu,memory}.max``) or v1
    (``/sys/fs/cgroup/{cpu,memory}/cpu.cfs_quota_us``, etc.). All numeric
    fields are ``None`` when the corresponding file is absent or
    unparsable; ``-1`` means "max"/unlimited.
    """

    version: str  # "v2" | "v1" | "unknown"
    cpu_quota_milli: int | None  # -1 = unlimited, None = unknown
    cpu_period_us: int | None
    memory_limit_mib: int | None  # -1 = unlimited
    memory_current_mib: int | None
    memory_swap_limit_mib: int | None
    pids_limit: int | None


@dataclass(frozen=True)
class RLimitSnapshot:
    """Selected ``resource.getrlimit`` entries (soft, hard)."""

    nofile: tuple[int, int] | None  # RLIMIT_NOFILE
    nproc: tuple[int, int] | None  # RLIMIT_NPROC
    address_space: tuple[int, int] | None  # RLIMIT_AS
    data: tuple[int, int] | None  # RLIMIT_DATA
    rss: tuple[int, int] | None  # RLIMIT_RSS (Linux)


@dataclass(frozen=True)
class RuntimeInfo:
    """Python runtime + known concurrency-relevant env vars."""

    python_version: str
    python_platform: str
    maxsize: int
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceContext:
    """Snapshot of the process's effective resource envelope."""

    pid: int
    ppid: int
    host: HostInfo
    cgroup: CgroupLimits
    rlimits: RLimitSnapshot
    runtime: RuntimeInfo

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _collect_host() -> HostInfo:
    cpu_count = os.cpu_count()
    mem_total_mib: int | None = None
    mem_available_mib: int | None = None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            text = meminfo.read_text()
            for line in text.splitlines():
                parts = line.split()
                if len(parts) < 2:
                    continue
                key = parts[0].rstrip(":")
                try:
                    value_kb = int(parts[1])
                except ValueError:
                    continue
                if key == "MemTotal":
                    mem_total_mib = value_kb // 1024
                elif key == "MemAvailable":
                    mem_available_mib = value_kb // 1024
        except OSError:
            log.debug("Failed to read /proc/meminfo", exc_info=True)
    return HostInfo(
        cpu_count=cpu_count,
        memory_total_mib=mem_total_mib,
        memory_available_mib=mem_available_mib,
    )


def _collect_cgroup_v2() -> CgroupLimits | None:
    root = Path("/sys/fs/cgroup")
    cpu_max = root / "cpu.max"
    memory_max = root / "memory.max"
    if not (cpu_max.exists() or memory_max.exists()):
        return None

    cpu_quota_milli: int | None = None
    cpu_period_us: int | None = None
    if cpu_max.exists():
        try:
            quota, period = _parse_cpu_max_v2(cpu_max.read_text())
            cpu_period_us = period
            if quota is None:
                cpu_quota_milli = -1
            else:
                # Convert quota/period (both in microseconds) to CPU milli.
                # quota/period = cpu-cores; × 1000 = milli.
                cpu_quota_milli = int(round(quota * 1000 / period))
        except OSError:
            pass

    def _mib(n: int | None) -> int | None:
        if n is None:
            return None
        if n < 0:
            return -1
        return n // (1024 * 1024)

    return CgroupLimits(
        version="v2",
        cpu_quota_milli=cpu_quota_milli,
        cpu_period_us=cpu_period_us,
        memory_limit_mib=_mib(_read_int(memory_max)),
        memory_current_mib=_mib(_read_int(root / "memory.current")),
        memory_swap_limit_mib=_mib(_read_int(root / "memory.swap.max")),
        pids_limit=_read_int(root / "pids.max"),
    )


def _collect_cgroup_v1() -> CgroupLimits | None:
    cpu_root = Path("/sys/fs/cgroup/cpu")
    mem_root = Path("/sys/fs/cgroup/memory")
    if not (cpu_root.exists() or mem_root.exists()):
        return None

    quota = _read_int(cpu_root / "cpu.cfs_quota_us")
    period = _read_int(cpu_root / "cpu.cfs_period_us")
    cpu_quota_milli: int | None = None
    if quota is not None and period:
        cpu_quota_milli = -1 if quota < 0 else int(round(quota * 1000 / period))

    def _mib(n: int | None) -> int | None:
        if n is None:
            return None
        # v1 encodes "no limit" as 9223372036854771712 (close to int64 max);
        # treat huge values as unlimited.
        if n < 0 or n > (1 << 62):
            return -1
        return n // (1024 * 1024)

    return CgroupLimits(
        version="v1",
        cpu_quota_milli=cpu_quota_milli,
        cpu_period_us=period,
        memory_limit_mib=_mib(_read_int(mem_root / "memory.limit_in_bytes")),
        memory_current_mib=_mib(_read_int(mem_root / "memory.usage_in_bytes")),
        memory_swap_limit_mib=_mib(_read_int(mem_root / "memory.memsw.limit_in_bytes")),
        pids_limit=_read_int(Path("/sys/fs/cgroup/pids/pids.max")),
    )


def _collect_cgroup() -> CgroupLimits:
    return (
        _collect_cgroup_v2()
        or _collect_cgroup_v1()
        or CgroupLimits(
            version="unknown",
            cpu_quota_milli=None,
            cpu_period_us=None,
            memory_limit_mib=None,
            memory_current_mib=None,
            memory_swap_limit_mib=None,
            pids_limit=None,
        )
    )


def _getrlimit(key: int) -> tuple[int, int] | None:
    try:
        return resource.getrlimit(key)
    except (ValueError, OSError):
        return None


def _collect_rlimits() -> RLimitSnapshot:
    # RLIMIT_RSS is Linux-only; guard the lookup.
    rss_key = getattr(resource, "RLIMIT_RSS", None)
    return RLimitSnapshot(
        nofile=_getrlimit(resource.RLIMIT_NOFILE),
        nproc=_getrlimit(resource.RLIMIT_NPROC) if hasattr(resource, "RLIMIT_NPROC") else None,
        address_space=_getrlimit(resource.RLIMIT_AS),
        data=_getrlimit(resource.RLIMIT_DATA),
        rss=_getrlimit(rss_key) if rss_key is not None else None,
    )


def _redact(name: str, value: str) -> str:
    up = name.upper()
    if any(tok in up for tok in _REDACTED_TOKENS):
        return f"<redacted:{len(value)}chars>"
    return value


def _collect_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in os.environ.items():
        if any(name.startswith(prefix) or name == prefix for prefix in _KNOWN_ENV_PREFIXES):
            out[name] = _redact(name, value)
    return dict(sorted(out.items()))


def _collect_runtime() -> RuntimeInfo:
    return RuntimeInfo(
        python_version=sys.version.split()[0],
        python_platform=sys.platform,
        maxsize=sys.maxsize,
        env=_collect_env(),
    )


def collect_resource_context() -> ResourceContext:
    """Assemble the full resource snapshot for the current process."""
    return ResourceContext(
        pid=os.getpid(),
        ppid=os.getppid(),
        host=_collect_host(),
        cgroup=_collect_cgroup(),
        rlimits=_collect_rlimits(),
        runtime=_collect_runtime(),
    )


def _fmt_mib(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value == -1:
        return "unlimited"
    if value >= 1024:
        return f"{value / 1024:.1f} GiB ({value} MiB)"
    return f"{value} MiB"


def _fmt_cpu_milli(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value == -1:
        return "unlimited"
    return f"{value} milli ({value / 1000:.2f} cores)"


def _fmt_rlimit(pair: tuple[int, int] | None) -> str:
    if pair is None:
        return "unknown"
    soft, hard = pair
    _INF = resource.RLIM_INFINITY

    def _one(v: int) -> str:
        return "unlimited" if v == _INF else str(v)

    return f"soft={_one(soft)} hard={_one(hard)}"


def format_resource_context(ctx: ResourceContext) -> str:
    """Pretty, multi-line dump suitable for container-startup logs."""
    lines: list[str] = []
    lines.append(f"=== resource context (pid {ctx.pid}, ppid {ctx.ppid}) ===")
    lines.append("[host]")
    lines.append(f"  cpu_count        = {ctx.host.cpu_count}")
    lines.append(f"  memory_total     = {_fmt_mib(ctx.host.memory_total_mib)}")
    lines.append(f"  memory_available = {_fmt_mib(ctx.host.memory_available_mib)}")
    lines.append(f"[cgroup {ctx.cgroup.version}]")
    lines.append(f"  cpu_quota        = {_fmt_cpu_milli(ctx.cgroup.cpu_quota_milli)}")
    if ctx.cgroup.cpu_period_us:
        lines.append(f"  cpu_period_us    = {ctx.cgroup.cpu_period_us}")
    lines.append(f"  memory_limit     = {_fmt_mib(ctx.cgroup.memory_limit_mib)}")
    lines.append(f"  memory_current   = {_fmt_mib(ctx.cgroup.memory_current_mib)}")
    lines.append(f"  memory_swap_max  = {_fmt_mib(ctx.cgroup.memory_swap_limit_mib)}")
    if ctx.cgroup.pids_limit is not None:
        lines.append(f"  pids_limit       = {ctx.cgroup.pids_limit}")
    lines.append("[rlimits]")
    lines.append(f"  nofile           = {_fmt_rlimit(ctx.rlimits.nofile)}")
    if ctx.rlimits.nproc is not None:
        lines.append(f"  nproc            = {_fmt_rlimit(ctx.rlimits.nproc)}")
    lines.append(f"  address_space    = {_fmt_rlimit(ctx.rlimits.address_space)}")
    lines.append(f"  data             = {_fmt_rlimit(ctx.rlimits.data)}")
    if ctx.rlimits.rss is not None:
        lines.append(f"  rss              = {_fmt_rlimit(ctx.rlimits.rss)}")
    lines.append("[runtime]")
    lines.append(
        f"  python           = {ctx.runtime.python_version} ({ctx.runtime.python_platform})"
    )
    lines.append(f"  maxsize          = {ctx.runtime.maxsize}")
    lines.append("[env]")
    if not ctx.runtime.env:
        lines.append("  (no tracked env vars set)")
    else:
        key_w = max(len(k) for k in ctx.runtime.env)
        for k in sorted(ctx.runtime.env):
            lines.append(f"  {k.ljust(key_w)} = {ctx.runtime.env[k]}")
    lines.append("=== end resource context ===")
    return "\n".join(lines)


def log_resource_context(
    target: logging.Logger | None = None,
    level: int = logging.INFO,
) -> ResourceContext:
    """Collect and log the resource context; return the context for tests."""
    ctx = collect_resource_context()
    target = target or log
    for line in format_resource_context(ctx).splitlines():
        target.log(level, line)
    return ctx
