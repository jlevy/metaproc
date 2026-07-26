"""Resource-aware process pool with adaptive concurrency.

Manages coding agent subprocesses with dynamic concurrency based on
real-time system memory pressure, per-process health monitoring, and
resource limit enforcement.
"""

from metaproc.runpool.backend import (
    HealthMetrics,
    LaunchBackend,
    LaunchHandle,
    LocalBackend,
    PreparedLaunch,
)
from metaproc.runpool.events import EventLogger
from metaproc.runpool.pool import (
    ProcessConfig,
    ProcessResult,
    RunPool,
    RunPoolConfig,
    resolve_host_max_concurrency,
)
from metaproc.runpool.registry import (
    available_backends,
    get_backend,
    register_backend,
)
from metaproc.runpool.status import (
    PressureStatus,
    ProcessStatus,
    RunPoolStatus,
    is_pool_alive,
    read_status,
    write_status,
)

__all__ = [
    "EventLogger",
    "HealthMetrics",
    "LaunchBackend",
    "LaunchHandle",
    "LocalBackend",
    "PreparedLaunch",
    "PressureStatus",
    "ProcessConfig",
    "ProcessResult",
    "ProcessStatus",
    "RunPool",
    "RunPoolConfig",
    "RunPoolStatus",
    "available_backends",
    "get_backend",
    "is_pool_alive",
    "read_status",
    "register_backend",
    "resolve_host_max_concurrency",
    "write_status",
]
