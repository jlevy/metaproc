"""The pressure engine: pure policy over normalized evidence.

Everything here is a function of the samples it is given and the clock values it is
told. It never reads the host, sleeps, or signals; the monitor does those and feeds the
results back. That is what makes replay possible: the same samples with the same times
produce the same decisions.

The rules are the memory guard's, and each one was paid for:

- A projection may hold work back; only a measured state may take work away.
- The producer is paused before any harvest, as a bounded duty cycle across every
  spawner, because shedding downstream of a running producer is a race the guard loses.
- A critical alarm never counts as recovered.
- Fault is assessed before any victim is taken and recomputed every danger sample.
- Abort needs a failing host and exhausted shedding, together.
- Lateness is diagnosis, never a trigger.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from safeproc.models import (
    ALARM_CRITICAL,
    ALARM_WARNING,
    PREDICTIVE_REASONS,
    Action,
    ActionKind,
    Candidate,
    DangerReason,
    Decision,
    GuardPolicy,
    HostSample,
    OutsideReading,
    PressureState,
    TreeSample,
)

Trend = deque[tuple[float, float]]


def compressor_rate(trend: Sequence[tuple[float, float]]) -> float:
    """GB/s of compressor growth across the trailing window, 0 when not growing."""
    if len(trend) < 2:
        return 0.0
    (t0, c0), (t1, c1) = trend[0], trend[-1]
    span = t1 - t0
    return max(0.0, (c1 - c0) / span) if span > 0 else 0.0


def reclaimable_fall(trend: Sequence[tuple[float, float]]) -> float:
    """GB/s that reclaimable memory is FALLING, 0 when flat or recovering."""
    if len(trend) < 2:
        return 0.0
    (t0, r0), (t1, r1) = trend[0], trend[-1]
    span = t1 - t0
    return max(0.0, (r0 - r1) / span) if span > 0 else 0.0


def danger_reason(
    host: HostSample,
    policy: GuardPolicy,
    *,
    compressor_gbs: float = 0.0,
    fall_gbs: float = 0.0,
    workers: int = 0,
) -> DangerReason | None:
    """Which trigger, if any, says the host is in trouble. ``None`` means healthy.

    Independent conditions, deliberately not collapsed into one score: they mean
    different things and the journal should say which one fired. Starvation is not a
    trigger; see the engine's heartbeat handling.
    """
    if host.suspension_gb < policy.danger_suspension_gb:
        return DangerReason.SWAP_LINE
    if host.pressure >= ALARM_WARNING and host.ancm_ratio < policy.danger_ancm_ratio:
        return DangerReason.RATIO
    if host.pressure >= ALARM_CRITICAL:
        return DangerReason.PRESSURE
    if host.stall_full_pct is not None and host.stall_full_pct >= policy.stall_full_pct:
        return DangerReason.STALL
    floor = policy.effective_floor_gb(host)
    if host.pressure >= policy.danger_pressure and host.reclaimable_gb < floor:
        return DangerReason.FLOOR
    if (
        host.pressure >= ALARM_WARNING
        and fall_gbs > 0.0
        and (host.reclaimable_gb - floor) / fall_gbs < policy.reaction_window_s
    ):
        return DangerReason.ETA
    if host.pressure >= ALARM_WARNING and compressor_gbs >= policy.compressor_rate_gbs:
        return DangerReason.SLOPE
    if policy.pool_limit is not None and workers > policy.pool_limit:
        return DangerReason.POOL
    return None


def select_batch(
    workers: Sequence[Candidate], policy: GuardPolicy, *, floor: int = 0
) -> list[Candidate]:
    """One proportional round: the largest victims whose cost reaches the shed fraction.

    Sized by memory rather than count so one rule covers both shapes: fifty equal workers
    lose five, while fifty where one holds a quarter of the memory lose only that one.
    Capped at ``max_batch``, and never fewer than ``floor`` when a pool limit demands a
    count regardless of memory.
    """
    if not workers:
        return []
    pool = sorted(workers, key=lambda candidate: -candidate.cost_mb)
    budget_mb = sum(candidate.cost_mb for candidate in pool) * policy.shed_fraction
    freed = 0.0
    batch: list[Candidate] = []
    while pool:
        need_floor = len(batch) < floor
        want_budget = len(batch) < policy.max_batch and (not batch or freed < budget_mb)
        if not need_floor and not want_budget:
            break
        victim = pool.pop(0)
        batch.append(victim)
        freed += victim.cost_mb
    return batch


def host_is_failing(host: HostSample, policy: GuardPolicy) -> bool:
    """The measured bar for abort: below half the floor, critical under it, or suspending."""
    floor = policy.effective_floor_gb(host)
    return (
        host.reclaimable_gb < floor / 2
        or (host.pressure >= ALARM_CRITICAL and host.reclaimable_gb < floor)
        or host.suspension_gb < policy.critical_suspension_gb
    )


def must_abort(host: HostSample, policy: GuardPolicy, *, spent: bool, workers: int) -> bool:
    """Whether taking the whole tree is the only option left.

    Exhausting shed rounds is not by itself a reason to destroy a run; an earlier build
    treated it as one and killed four consecutive runs with 6 GB reclaimable. Not
    aborting at all ended in a kernel panic. Both a failing host and exhausted shedding
    are required.
    """
    if not host_is_failing(host, policy):
        return False
    return spent or workers == 0


@dataclass(frozen=True)
class Cadence:
    """How well the monitor is keeping its own schedule. Late means the reading is stale."""

    since_last_s: float
    lag_s: float


class PressureEngine:
    """The stateful policy for one monitored tree. Pure with respect to injected time."""

    def __init__(self, policy: GuardPolicy) -> None:
        self.policy = policy
        self._danger_since: float | None = None
        self._clear = 0
        self._shed_rounds = 0
        self._shed_episode = 0
        self._shed_total = 0
        self._next_round_at = 0.0
        self._repause_at = 0.0
        self._paused = False
        self._paused_at: float | None = None
        self._paused_total_s = 0.0
        self._pauses = 0
        self._blamed = False
        self._held_noted = False
        self._outside_gb = 0.0
        self._compressor: Trend = deque()
        self._reclaimable: Trend = deque()
        self._last_host: HostSample | None = None
        self._last_sample_at: float | None = None
        self._late_heartbeats = 0
        self._aborted = False
        self._last_cadence = Cadence(0.0, 0.0)

    # ── read-only state ──────────────────────────────────────────────────────

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def shed_total(self) -> int:
        return self._shed_total

    @property
    def pauses(self) -> int:
        return self._pauses

    @property
    def late_heartbeats(self) -> int:
        return self._late_heartbeats

    @property
    def last_cadence(self) -> Cadence:
        return self._last_cadence

    def paused_total_s(self, now: float) -> float:
        """Seconds spent paused so far, including an open pause."""
        open_pause = (now - self._paused_at) if self._paused_at is not None else 0.0
        return self._paused_total_s + open_pause

    def needs_accuracy(self) -> bool:
        """Whether the next sample must measure attributable cost rather than RSS.

        Accuracy is bought whenever the host is anywhere near unhappy, not only when one
        signal is moving. Gating it on the compressor slope alone cost three runs: danger
        arrived by another route, RSS had been hollowed out by compression, nothing
        qualified to shed, and the guard escalated straight to killing the tree.
        """
        last = self._last_host
        if last is not None and (
            last.pressure >= ALARM_WARNING or last.reclaimable_gb < self.policy.warn_gb
        ):
            return True
        if last is not None and last.suspension_gb < self.policy.disk_coupling_gb:
            return True
        return compressor_rate(self._compressor) > 0.01

    def rebase_cadence(self, now: float) -> None:
        """Treat time spent acting as deliberate, not as starvation.

        A harvest takes seconds. Without this, its own duration reads as lag on the next
        sample, and an earlier build that let lag become a trigger shed four times at
        pressure 1 with 12 GB free.
        """
        self._last_sample_at = now

    # ── the decision ─────────────────────────────────────────────────────────

    def evaluate(
        self,
        now: float,
        host: HostSample,
        tree: TreeSample,
        workers: Sequence[Candidate],
        outside: Callable[[], OutsideReading],
    ) -> Decision:
        """One sample in, one decision out. ``outside`` is called only when fault matters."""
        policy = self.policy
        actions: list[Action] = []

        since_last = (now - self._last_sample_at) if self._last_sample_at is not None else 0.0
        lag = max(0.0, since_last - policy.interval_s) if self._last_sample_at is not None else 0.0
        self._last_cadence = Cadence(since_last, lag)
        self._last_sample_at = now
        if lag >= policy.heartbeat_lag_s:
            self._late_heartbeats += 1
            actions.append(
                Action(
                    ActionKind.HEARTBEAT_LATE,
                    detail={
                        "since_last_s": round(since_last, 2),
                        "lag_s": round(lag, 2),
                        "interval_s": policy.interval_s,
                    },
                )
            )

        self._push(self._compressor, now, host.compressed_gb)
        self._push(self._reclaimable, now, host.reclaimable_gb)
        rate = compressor_rate(self._compressor)
        fall = reclaimable_fall(self._reclaimable)
        reason = danger_reason(
            host, policy, compressor_gbs=rate, fall_gbs=fall, workers=tree.workers
        )
        pool_hit = reason is DangerReason.POOL

        self._pace_pause(
            now, actions, mid_episode=reason is not None and self._danger_since is not None
        )

        if reason is None:
            state = self._recover(now, host, actions)
        else:
            state = self._danger(now, host, tree, workers, outside, reason, pool_hit, actions)

        self._last_host = host
        return Decision(
            state=state,
            reason=reason,
            danger_held_s=(now - self._danger_since) if self._danger_since is not None else 0.0,
            actions=tuple(actions),
            paused=self._paused,
            shed_total=self._shed_total,
            compressor_rate_gbs=rate,
            reclaimable_fall_gbs=fall,
            needs_accuracy=self.needs_accuracy(),
        )

    # ── pieces ───────────────────────────────────────────────────────────────

    def _push(self, trend: Trend, now: float, value: float) -> None:
        trend.append((now, value))
        while trend and now - trend[0][0] > self.policy.rate_window_s:
            trend.popleft()

    def _pause(self, now: float, actions: list[Action], why: str) -> None:
        if self._paused:
            return
        self._paused = True
        self._paused_at = now
        self._pauses += 1
        actions.append(Action(ActionKind.PAUSE, detail={"why": why}))

    def _resume(self, now: float, actions: list[Action], why: str) -> None:
        if not self._paused:
            return
        self._paused = False
        if self._paused_at is not None:
            self._paused_total_s += now - self._paused_at
        self._paused_at = None
        actions.append(Action(ActionKind.RESUME, detail={"why": why}))

    def _pace_pause(self, now: float, actions: list[Action], *, mid_episode: bool) -> None:
        """Duty-cycle the producer while one danger episode persists.

        The cap protects the coordinator's children; the re-pause protects the host. An
        earlier build resumed at the cap and never paused again until a new episode
        opened, so every cap became a release into a critical host.
        """
        policy = self.policy
        if (
            self._paused
            and self._paused_at is not None
            and now - self._paused_at >= policy.max_pause_s
        ):
            self._resume(now, actions, "pause cap reached; service window")
            self._repause_at = now + policy.min_run_s
            return
        if mid_episode and not self._paused and now >= self._repause_at:
            self._pause(now, actions, "re-pause while danger persists")

    def _recover(self, now: float, host: HostSample, actions: list[Action]) -> PressureState:
        self._danger_since = None
        self._blamed = False
        self._held_noted = False
        self._next_round_at = 0.0
        self._outside_gb = 0.0
        self._clear += 1
        if self._clear >= self.policy.recover_samples:
            self._resume(now, actions, "recovered")
            if self._shed_episode:
                self._shed_episode = 0
                self._shed_rounds = 0
        if host.pressure >= ALARM_WARNING or host.reclaimable_gb < self.policy.warn_gb:
            return PressureState.WATCH
        return PressureState.HEALTHY

    def _danger(
        self,
        now: float,
        host: HostSample,
        tree: TreeSample,
        workers: Sequence[Candidate],
        outside: Callable[[], OutsideReading],
        reason: DangerReason,
        pool_hit: bool,
        actions: list[Action],
    ) -> PressureState:
        policy = self.policy
        self._clear = 0
        if self._danger_since is None:
            self._danger_since = now
            # Pause immediately, before any confirmation: confirmation exists so a
            # transient dip does not cost a process; pausing costs nothing but latency.
            self._pause(now, actions, f"danger opened: {reason}")
        held = now - self._danger_since

        if held >= policy.confirm_s / 2:
            reading = outside()
            self._outside_gb = reading.total_gb
            if not self._blamed:
                self._blamed = True
                ranked = sorted(reading.by_pid.items(), key=lambda item: -item[1])[:6]
                actions.append(
                    Action(
                        ActionKind.BLAME,
                        pids=tuple(pid for pid, _ in ranked),
                        detail={
                            "reclaimable_gb": round(host.reclaimable_gb, 2),
                            "tree_cost_gb": round(tree.cost_gb, 2),
                            "outside_gb": round(reading.total_gb, 2),
                            "outside": [
                                {"pid": pid, "gb": round(mb / 1024, 2)} for pid, mb in ranked
                            ],
                        },
                    )
                )

        if reason in PREDICTIVE_REASONS:
            if not self._held_noted:
                self._held_noted = True
                actions.append(
                    Action(
                        ActionKind.PREDICTIVE_HOLD,
                        detail={
                            "reason": str(reason),
                            "reclaimable_gb": round(host.reclaimable_gb, 2),
                            "pressure": host.pressure,
                        },
                    )
                )
            return PressureState.EMBARGO

        if held < policy.confirm_s or now < self._next_round_at:
            return PressureState.CRITICAL

        if self._aborted:
            return PressureState.CATASTROPHIC

        spent = self._shed_rounds >= policy.max_shed_rounds
        if self._outside_gb > tree.cost_gb:
            # Decided BEFORE any worker is taken. An earlier build shed its own tree first
            # and only asked whose fault the pressure was once nothing was left.
            if not self._held_noted:
                self._held_noted = True
                actions.append(
                    Action(
                        ActionKind.HOLD_NOT_AT_FAULT,
                        detail={
                            "outside_gb": round(self._outside_gb, 2),
                            "tree_cost_gb": round(tree.cost_gb, 2),
                        },
                    )
                )
            return PressureState.CRITICAL

        if workers and not spent:
            floor = (
                max(0, tree.workers - policy.pool_limit)
                if pool_hit and policy.pool_limit is not None
                else 0
            )
            batch = select_batch(workers, policy, floor=floor)
            if batch:
                self._next_round_at = now + policy.shed_settle_s
                self._shed_rounds += 1
                self._shed_episode += len(batch)
                self._shed_total += len(batch)
                actions.append(
                    Action(
                        ActionKind.SHED,
                        pids=tuple(victim.pid for victim in batch),
                        detail={
                            "round": self._shed_rounds,
                            "freed_mb_est": round(sum(v.cost_mb for v in batch), 1),
                            "fraction": policy.shed_fraction,
                        },
                    )
                )
            return PressureState.CRITICAL

        if must_abort(host, policy, spent=spent, workers=len(workers)):
            self._aborted = True
            actions.append(
                Action(
                    ActionKind.ABORT,
                    detail={
                        "rounds": self._shed_rounds,
                        "shed_total": self._shed_total,
                        "tree_cost_gb": round(tree.cost_gb, 2),
                        "reclaimable_gb": round(host.reclaimable_gb, 2),
                    },
                )
            )
            return PressureState.CATASTROPHIC

        if not self._held_noted:
            self._held_noted = True
            actions.append(
                Action(
                    ActionKind.HOLD_SPENT,
                    detail={
                        "rounds": self._shed_rounds,
                        "reclaimable_gb": round(host.reclaimable_gb, 2),
                        "pressure": host.pressure,
                    },
                )
            )
        return PressureState.CRITICAL
