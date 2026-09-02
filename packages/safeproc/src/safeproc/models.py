"""Neutral models shared by every mode, provider, and surface.

Every memory value names its scope, every threshold is host-wide reclaimable memory, and
the policy defaults carry the memory guard's calibration: 8,000 one-second samples across
eleven runs on a 34 GB host, plus the failures recorded in its journal corpus. The
rationale for each default is on the field, because a maintainer changing one should
know what it cost to learn.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Scope(StrEnum):
    """What a memory observation describes. Lower scope is never promoted silently."""

    ROOT = "root"
    TREE = "tree"
    CGROUP = "cgroup"
    HOST = "host"


class SupervisionMode(StrEnum):
    """Owned launch establishes authority before execution; monitoring never does."""

    OWNED = "owned"
    MONITORED = "monitored"


class PlatformName(StrEnum):
    DARWIN = "darwin"
    LINUX = "linux"
    FAKE = "fake"


ALARM_NORMAL = 1
ALARM_WARNING = 2
ALARM_CRITICAL = 4
"""The kernel pressure alarm levels, as macOS defines them and as Linux is normalized to.

macOS ``kern.memorystatus_vm_pressure_level`` reports 1, 2, or 4, matching the
``dispatch/source.h`` constants. An alarm, never a budget.
"""


@dataclass(frozen=True)
class HostSample:
    """One host-wide reading. Host fields decide; everything else is recorded for tuning.

    ``reclaimable_gb`` is the budget: what the kernel can hand to a new allocation. On
    macOS that is free plus inactive plus purgeable pages; on Linux it is ``MemAvailable``
    bounded by the caller's own cgroup headroom. ``pressure`` is the normalized alarm.
    Fields a platform cannot supply are ``None`` or their neutral value, and the
    provider's capability record says which.
    """

    platform: PlatformName
    reclaimable_gb: float
    free_gb: float
    pressure: int
    wired_gb: float = 0.0
    compressed_gb: float = 0.0
    swap_used_mb: float = 0.0
    swap_total_mb: float = 0.0
    disk_gb: float = 999.0
    ancm_ratio: float = 1.0
    total_gb: float | None = None
    cgroup_headroom_gb: float | None = None
    stall_some_pct: float | None = None
    stall_full_pct: float | None = None
    swapin_rate_per_s: float | None = None

    @property
    def suspension_gb(self) -> float:
        """Distance to the line where macOS starts suspending applications.

        Free disk on the swap volume plus unused allocated swap. Platforms without that
        failure mode report a large disk figure, which keeps this far above every line.
        """
        return self.disk_gb + max(0.0, self.swap_total_mb - self.swap_used_mb) / 1024


@dataclass(frozen=True)
class TreeSample:
    """What the monitored tree looks like at one sample.

    ``cost_gb`` is attributable cost when ``measured``, RSS otherwise; the flag is recorded
    because the two are not comparable and a rollup that mixed them would be meaningless.
    """

    procs: int
    workers: int
    cost_gb: float
    rss_gb: float
    measured: bool
    worker_cost_mb: tuple[float, ...] = ()


@dataclass(frozen=True)
class Candidate:
    """A shed-able process as the engine sees it: enough to size a round, nothing more."""

    pid: int
    cost_mb: float
    age_s: float
    cmd: str = ""


@dataclass(frozen=True)
class OutsideReading:
    """Memory held outside the monitored tree, and who holds it."""

    total_gb: float
    by_pid: Mapping[int, float] = field(default_factory=dict[int, float])


class DangerReason(StrEnum):
    """Which trigger says the host is in trouble. Kept separate so a journal says which.

    Measured reasons say the host IS in trouble and authorize shedding. Predictive
    reasons say it is HEADED there and authorize only the pause, which costs latency
    and nothing else. Replayed over every recorded journal, the projection opened
    episodes on five runs that completed successfully as well as all four that died, so
    the split is a correctness requirement, not a tuning preference.
    """

    SWAP_LINE = "swap-line"
    """Within a few failed swapfile creations of macOS suspending applications."""

    RATIO = "ratio"
    """The kernel's own red-line arithmetic, one step before red, while under pressure."""

    PRESSURE = "pressure"
    """The platform critical alarm. Never counts as recovered."""

    FLOOR = "floor"
    """Reclaimable memory below the effective floor while the alarm is at danger level."""

    STALL = "stall"
    """Linux: sustained full stall, every non-idle task waiting on memory."""

    ETA = "eta"
    """Predictive: falling reclaimable memory crosses the floor inside the reaction window."""

    SLOPE = "slope"
    """Predictive: the compressor is climbing at warning pressure."""

    POOL = "pool"
    """The operator's optional count cap on shed-able workers."""


PREDICTIVE_REASONS: frozenset[DangerReason] = frozenset({DangerReason.ETA, DangerReason.SLOPE})


class PressureState(StrEnum):
    """The normalized host state the policy reports."""

    HEALTHY = "healthy"
    WATCH = "watch"
    EMBARGO = "embargo"
    CRITICAL = "critical"
    CATASTROPHIC = "catastrophic"


class ActionKind(StrEnum):
    """What the engine asks the actuator to do. Observation mode records, never acts."""

    PAUSE = "pause"
    RESUME = "resume"
    SHED = "shed"
    ABORT = "abort"
    HOLD_NOT_AT_FAULT = "hold_not_at_fault"
    PREDICTIVE_HOLD = "predictive_hold"
    BLAME = "blame"
    HEARTBEAT_LATE = "heartbeat_late"
    HOLD_SPENT = "hold_spent"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    pids: tuple[int, ...] = ()
    detail: Mapping[str, object] = field(default_factory=dict[str, object])


@dataclass(frozen=True)
class Decision:
    """The engine's answer for one sample."""

    state: PressureState
    reason: DangerReason | None
    danger_held_s: float
    actions: tuple[Action, ...]
    paused: bool
    shed_total: int
    compressor_rate_gbs: float
    reclaimable_fall_gbs: float
    needs_accuracy: bool
    """Whether the next sample must measure attributable cost rather than RSS."""

    @property
    def measured(self) -> bool:
        return self.reason is not None and self.reason not in PREDICTIVE_REASONS


@dataclass(frozen=True)
class GuardPolicy:
    """Every threshold, with the failure that set it.

    All memory thresholds are host-wide reclaimable memory, never per-process or
    per-tree. The floor stays at the measured crash band because every gigabyte of floor
    is a gigabyte the workload cannot use; the safety margin is supplied in time and by
    the disk correction.
    """

    intervene: bool = False
    """Observation is the default. Nothing is signalled unless this is set."""

    dry_run: bool = False
    """Under ``intervene``, decide and journal every action but signal nothing."""

    danger_gb: float = 3.0
    """Host-wide reclaimable floor at the measured crash band. Every failure ended below."""

    danger_pressure: int = ALARM_CRITICAL
    """Alarm required alongside the floor. Level 2 alone never preceded a failure."""

    warn_gb: float = 5.0
    """Reclaimable below which to log loudly and buy accurate footprints. Nothing acts."""

    reaction_window_s: float = 20.0
    """Falling on course to cross the floor within this window is danger now."""

    confirm_s: float = 2.0
    """Wall-clock seconds danger must persist before shedding. Never sample counts."""

    compressor_rate_gbs: float = 0.05
    """Compressor growth at warning pressure that is predictive danger on its own."""

    rate_window_s: float = 15.0
    """Trailing window the slopes are measured over."""

    disk_coupling_gb: float = 8.0
    """Suspension-distance headroom below which the floor rises one for one."""

    danger_suspension_gb: float = 4.0
    """Suspension distance below which the host is in measured danger."""

    critical_suspension_gb: float = 1.5
    """Below this, suspension is imminent and abort treats the host as failing."""

    danger_ancm_ratio: float = 0.40
    """The kernel's red-line recovery bound; triggering here acts one step before red."""

    stall_full_pct: float = 25.0
    """Linux: full-stall share over ten seconds that is measured danger. Uncalibrated."""

    recover_samples: int = 5
    """Consecutive clear samples before the producer resumes and counters reset."""

    pool_limit: int | None = None
    """Optional cap on shed-able workers. Unset means the host decides."""

    min_worker_mb: float = 512.0
    """Ignore smaller processes when shedding: skip the shim, take the worker."""

    shed_fraction: float = 0.10
    """Fraction of the tree's memory to remove per round, largest first."""

    shed_settle_s: float = 2.0
    """Earliest next round after one; sampling continues through it."""

    max_batch: int = 8
    """Most victims one round may take, however large the tree."""

    min_run_s: float = 1.5
    """Service window between pauses while one episode persists."""

    max_pause_s: float = 8.0
    """Hard cap on a pause. A supervisor frozen past its children's deadlines loses them."""

    heartbeat_lag_s: float = 2.0
    """Lag beyond the interval that counts as the monitor being starved. Diagnostic only."""

    max_shed_rounds: int = 5
    """Rounds per episode before shedding stops and the hold-or-abort choice is made."""

    term_grace_s: float = 1.0
    """Seconds a victim gets to handle SIGTERM before SIGKILL."""

    interval_s: float = 0.5
    """Target seconds between samples."""

    snapshot_interval_s: float = 60.0
    """Seconds between structured tree snapshots in the journal."""

    worker_patterns: tuple[str, ...] = ()
    """Only shed descendants whose argv contains one of these. Empty means any."""

    def effective_floor_gb(self, host: HostSample) -> float:
        """The operator's floor, disk-corrected.

        A host whose swap can no longer grow gives back the missing headroom to RAM,
        gigabyte for gigabyte, because that is where the un-spillable pages now live.
        """
        return self.danger_gb + max(0.0, self.disk_coupling_gb - host.suspension_gb)


@dataclass(frozen=True)
class ResourceProfile:
    """A process's memory shape for admission: not one cost, a startup curve.

    Used by owned launch and the broker in later phases; defined here so journals and
    profiles share one vocabulary from the first record.
    """

    steady_mb: float
    startup_peak_mb: float
    startup_window_s: float = 0.0
    launch_spacing_s: float = 0.0
    identity: str = ""
