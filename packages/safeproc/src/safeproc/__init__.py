"""Process-tree monitoring, owned launch, and host-safety coordination.

Safeproc watches a process tree and, when the host is measurably about to fail, removes
the least it can to save it. Observation is the default; intervention is an explicit
policy. The supported public surface is what this module re-exports; ``_platform`` and
other underscored modules are internal.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from safeproc.clocks import ClockDomain
from safeproc.identity import ProcessIdentity, ProcessRecord, ProcessTarget
from safeproc.models import (
    Action,
    ActionKind,
    DangerReason,
    Decision,
    GuardPolicy,
    HostSample,
    PressureState,
    Scope,
    SupervisionMode,
    TreeSample,
)
from safeproc.monitor import MonitoredProcess, ProcessMonitor, WatchOutcome
from safeproc.policy import PressureEngine

try:
    __version__ = version("safeproc")
except PackageNotFoundError:  # pragma: no cover - source tree without metadata
    __version__ = "0.0.0"

__all__ = [
    "Action",
    "ActionKind",
    "ClockDomain",
    "DangerReason",
    "Decision",
    "GuardPolicy",
    "HostSample",
    "MonitoredProcess",
    "PressureEngine",
    "PressureState",
    "ProcessIdentity",
    "ProcessMonitor",
    "ProcessRecord",
    "ProcessTarget",
    "Scope",
    "SupervisionMode",
    "TreeSample",
    "WatchOutcome",
    "__version__",
]
