"""The pressure engine, tested against the failures that shaped it.

Each test names the guard behavior it protects. Times are driven by hand, so every
decision here is a pure function of the samples and the clock values given.
"""

from __future__ import annotations

from collections.abc import Sequence

from safeproc.models import (
    ALARM_CRITICAL,
    ALARM_WARNING,
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
from safeproc.policy import (
    PressureEngine,
    danger_reason,
    must_abort,
    select_batch,
)
from tests.conftest import host


def workers(n: int, cost_mb: float = 1000.0) -> list[Candidate]:
    return [Candidate(pid=1000 + i, cost_mb=cost_mb, age_s=30.0) for i in range(n)]


def tree_for(candidates: Sequence[Candidate], procs: int | None = None) -> TreeSample:
    return TreeSample(
        procs=procs if procs is not None else len(candidates) + 2,
        workers=len(candidates),
        cost_gb=sum(c.cost_mb for c in candidates) / 1024,
        rss_gb=sum(c.cost_mb for c in candidates) / 1024,
        measured=True,
        worker_cost_mb=tuple(sorted((c.cost_mb for c in candidates), reverse=True)),
    )


def kinds(decision: Decision) -> list[ActionKind]:
    return [action.kind for action in decision.actions]


def no_outside() -> OutsideReading:
    return OutsideReading(0.0)


class Run:
    """Drive an engine sample by sample at a fixed interval."""

    def __init__(self, policy: GuardPolicy | None = None, start: float = 100.0) -> None:
        self.policy = policy or GuardPolicy(intervene=True)
        self.engine = PressureEngine(self.policy)
        self.t = start
        self.decisions: list[Decision] = []

    def step(
        self,
        sample: HostSample,
        candidates: Sequence[Candidate] = (),
        *,
        outside_gb: float = 0.0,
        dt: float | None = None,
    ) -> Decision:
        self.t += self.policy.interval_s if dt is None else dt
        decision = self.engine.evaluate(
            self.t, sample, tree_for(candidates), candidates, lambda: OutsideReading(outside_gb)
        )
        self.decisions.append(decision)
        return decision

    def run(
        self, sample: HostSample, candidates: Sequence[Candidate], seconds: float, **kw: float
    ) -> list[Decision]:
        out: list[Decision] = []
        steps = int(seconds / self.policy.interval_s)
        for _ in range(steps):
            out.append(self.step(sample, candidates, **kw))
        return out


class TestDangerReason:
    def test_healthy_host_has_no_reason(self) -> None:
        assert danger_reason(host(12.0, 1), GuardPolicy()) is None

    def test_floor_requires_both_conditions(self) -> None:
        policy = GuardPolicy()
        assert danger_reason(host(2.0, ALARM_CRITICAL), policy) is DangerReason.PRESSURE
        assert danger_reason(host(2.0, ALARM_WARNING), policy) is None
        assert danger_reason(host(2.0, 1), policy) is None

    def test_pressure_four_is_danger_regardless_of_headroom(self) -> None:
        assert danger_reason(host(20.0, ALARM_CRITICAL), GuardPolicy()) is DangerReason.PRESSURE

    def test_floor_fires_at_configured_alarm(self) -> None:
        policy = GuardPolicy(danger_pressure=ALARM_WARNING, danger_gb=10.0)
        assert danger_reason(host(8.0, ALARM_WARNING), policy) is DangerReason.FLOOR

    def test_swap_line_outranks_everything_even_at_pressure_one(self) -> None:
        sample = host(20.0, 1, disk_gb=2.0, swap_total_mb=1024, swap_used_mb=1024)
        assert danger_reason(sample, GuardPolicy()) is DangerReason.SWAP_LINE

    def test_ratio_fires_one_step_before_red(self) -> None:
        sample = host(20.0, ALARM_WARNING, ancm_ratio=0.35)
        assert danger_reason(sample, GuardPolicy()) is DangerReason.RATIO

    def test_linux_full_stall_is_measured_danger(self) -> None:
        sample = host(20.0, 1, stall_full_pct=40.0)
        assert danger_reason(sample, GuardPolicy()) is DangerReason.STALL

    def test_eta_and_slope_are_predictive(self) -> None:
        policy = GuardPolicy()
        eta = danger_reason(host(4.0, ALARM_WARNING), policy, fall_gbs=0.2)
        slope = danger_reason(host(12.0, ALARM_WARNING), policy, compressor_gbs=0.1)
        assert eta is DangerReason.ETA
        assert slope is DangerReason.SLOPE
        assert eta not in {DangerReason.FLOOR, DangerReason.PRESSURE}

    def test_slope_needs_warning_pressure(self) -> None:
        assert danger_reason(host(12.0, 1), GuardPolicy(), compressor_gbs=1.0) is None

    def test_pool_limit_is_its_own_layer(self) -> None:
        policy = GuardPolicy(pool_limit=3)
        assert danger_reason(host(20.0, 1), policy, workers=5) is DangerReason.POOL
        assert danger_reason(host(20.0, 1), policy, workers=3) is None

    def test_disk_coupling_raises_the_floor(self) -> None:
        policy = GuardPolicy(danger_gb=3.0, disk_coupling_gb=8.0)
        tight = host(5.0, ALARM_CRITICAL, disk_gb=5.0, swap_total_mb=0, swap_used_mb=0)
        assert policy.effective_floor_gb(tight) == 6.0
        assert danger_reason(tight, policy) is DangerReason.PRESSURE
        spacious = host(5.0, ALARM_WARNING, disk_gb=100.0)
        assert policy.effective_floor_gb(spacious) == 3.0


class TestProportionalShedding:
    def test_fifty_equal_workers_lose_five(self) -> None:
        batch = select_batch(workers(50), GuardPolicy())
        assert len(batch) == 5

    def test_one_hog_loses_only_the_hog(self) -> None:
        pool = workers(49, 100.0) + [Candidate(pid=9, cost_mb=1700.0, age_s=5)]
        batch = select_batch(pool, GuardPolicy())
        assert [c.pid for c in batch] == [9]

    def test_batch_is_capped(self) -> None:
        batch = select_batch(workers(200, 10.0), GuardPolicy(max_batch=8))
        assert len(batch) == 8

    def test_pool_floor_overrides_the_cap(self) -> None:
        batch = select_batch(workers(50, 10.0), GuardPolicy(max_batch=8), floor=12)
        assert len(batch) == 12

    def test_empty_pool_sheds_nothing(self) -> None:
        assert select_batch([], GuardPolicy()) == []


class TestAbortIsLastResort:
    def test_spent_rounds_alone_do_not_abort(self) -> None:
        assert not must_abort(host(6.0, ALARM_CRITICAL), GuardPolicy(), spent=True, workers=3)

    def test_failing_host_with_spent_rounds_aborts(self) -> None:
        assert must_abort(host(1.0, ALARM_CRITICAL), GuardPolicy(), spent=True, workers=3)

    def test_failing_host_with_nothing_to_shed_aborts(self) -> None:
        assert must_abort(host(1.0, ALARM_CRITICAL), GuardPolicy(), spent=False, workers=0)

    def test_failing_host_with_rounds_left_holds(self) -> None:
        assert not must_abort(host(1.0, ALARM_CRITICAL), GuardPolicy(), spent=False, workers=3)

    def test_suspension_imminent_counts_as_failing(self) -> None:
        sample = host(20.0, 1, disk_gb=1.0, swap_total_mb=0, swap_used_mb=0)
        assert must_abort(sample, GuardPolicy(), spent=True, workers=1)


class TestPredictiveVersusMeasured:
    def test_a_projection_pauses_but_never_sheds(self) -> None:
        run = Run()
        pool = workers(10)
        first = run.step(host(12.0, ALARM_WARNING, compressed_gb=4.0), pool)
        assert first.reason is None
        # Compressor climbing at 0.2 GB/s across the window: predictive slope.
        decisions = [
            run.step(host(12.0, ALARM_WARNING, compressed_gb=4.0 + 0.1 * i), pool)
            for i in range(1, 30)
        ]
        opened = next(d for d in decisions if d.reason is not None)
        assert opened.reason is DangerReason.SLOPE
        assert opened.state is PressureState.EMBARGO
        assert ActionKind.PAUSE in kinds(opened)
        assert not any(ActionKind.SHED in kinds(d) for d in decisions)
        assert any(ActionKind.PREDICTIVE_HOLD in kinds(d) for d in decisions)

    def test_a_measured_state_sheds_after_confirmation(self) -> None:
        policy = GuardPolicy(intervene=True, confirm_s=2.0, interval_s=0.5)
        run = Run(policy)
        pool = workers(10)
        decisions = run.run(host(2.0, ALARM_CRITICAL), pool, seconds=3.0)
        assert decisions[0].state is PressureState.CRITICAL
        assert ActionKind.PAUSE in kinds(decisions[0])
        shed = [d for d in decisions if ActionKind.SHED in kinds(d)]
        assert shed, "a confirmed measured state must shed"
        first_shed = shed[0]
        assert first_shed.danger_held_s >= policy.confirm_s
        shed_action = next(a for a in first_shed.actions if a.kind is ActionKind.SHED)
        assert len(shed_action.pids) == 1  # 10% of ten equal workers

    def test_rounds_are_paced_by_the_settle_window(self) -> None:
        policy = GuardPolicy(intervene=True, confirm_s=1.0, interval_s=0.5, shed_settle_s=2.0)
        run = Run(policy)
        decisions = run.run(host(2.0, ALARM_CRITICAL), workers(40), seconds=6.0)
        shed_times = [d for d in decisions if ActionKind.SHED in kinds(d)]
        assert len(shed_times) >= 2
        # Between two rounds at least the settle window elapses.
        times = [run.decisions.index(d) * policy.interval_s for d in shed_times]
        assert all(b - a >= policy.shed_settle_s for a, b in zip(times, times[1:], strict=False))


class TestPressureFourNeverRecovers:
    def test_critical_alarm_with_headroom_stays_in_danger(self) -> None:
        run = Run()
        decisions = run.run(host(2.0, ALARM_CRITICAL), workers(3), seconds=2.0)
        assert all(d.reason is not None for d in decisions)
        # Headroom returns but the alarm stays critical: still danger, no resume.
        after = run.run(host(20.0, ALARM_CRITICAL), workers(3), seconds=5.0)
        assert all(d.reason is DangerReason.PRESSURE for d in after)
        assert not any(
            ActionKind.RESUME in kinds(d) for d in after if "recovered" in str(d.actions)
        )

    def test_recovery_needs_consecutive_clear_samples(self) -> None:
        policy = GuardPolicy(intervene=True, recover_samples=5, interval_s=0.5)
        run = Run(policy)
        run.run(host(2.0, ALARM_CRITICAL), workers(3), seconds=1.0)
        assert run.engine.paused
        clear = [run.step(host(20.0, 1), workers(3)) for _ in range(4)]
        assert all(ActionKind.RESUME not in kinds(d) for d in clear)
        assert run.engine.paused
        fifth = run.step(host(20.0, 1), workers(3))
        assert ActionKind.RESUME in kinds(fifth)
        assert not run.engine.paused
        assert fifth.state is PressureState.HEALTHY


class TestDutyCyclePause:
    def test_pause_ends_at_the_cap_and_repauses_after_the_service_window(self) -> None:
        policy = GuardPolicy(
            intervene=True, interval_s=0.5, max_pause_s=4.0, min_run_s=1.5, max_shed_rounds=0
        )
        run = Run(policy)
        sample = host(2.0, ALARM_CRITICAL)
        first = run.step(sample)
        assert ActionKind.PAUSE in kinds(first)
        decisions = run.run(sample, (), seconds=12.0)
        pauses = [i for i, d in enumerate(decisions) if ActionKind.PAUSE in kinds(d)]
        resumes = [i for i, d in enumerate(decisions) if ActionKind.RESUME in kinds(d)]
        assert resumes, "the cap must release the producer while danger persists"
        assert pauses, "the producer must be re-paused after the service window"
        first_resume = resumes[0]
        assert first_resume * policy.interval_s >= policy.max_pause_s - policy.interval_s
        next_pause = next(i for i in pauses if i > first_resume)
        assert (next_pause - first_resume) * policy.interval_s >= policy.min_run_s

    def test_paused_time_is_accounted(self) -> None:
        policy = GuardPolicy(intervene=True, interval_s=1.0, max_pause_s=3.0, max_shed_rounds=0)
        run = Run(policy)
        run.run(host(2.0, ALARM_CRITICAL), (), seconds=3.0)
        assert run.engine.paused_total_s(run.t) >= 2.0


class TestStarvationIsNotATrigger:
    def test_a_late_sample_is_journaled_not_acted_on(self) -> None:
        policy = GuardPolicy(intervene=True, interval_s=1.0, heartbeat_lag_s=2.0)
        run = Run(policy)
        run.step(host(12.0, 1))
        late = run.step(host(12.0, 1), dt=37.0)
        assert ActionKind.HEARTBEAT_LATE in kinds(late)
        assert late.state is PressureState.HEALTHY
        assert late.reason is None
        assert run.engine.late_heartbeats == 1

    def test_acting_time_is_rebased_not_counted_as_lag(self) -> None:
        policy = GuardPolicy(intervene=True, interval_s=1.0, heartbeat_lag_s=2.0)
        run = Run(policy)
        run.step(host(12.0, 1))
        run.engine.rebase_cadence(run.t + 30.0)
        run.t += 30.0
        decision = run.step(host(12.0, 1))
        assert ActionKind.HEARTBEAT_LATE not in kinds(decision)


class TestFaultAttribution:
    def test_outside_pressure_holds_instead_of_shedding(self) -> None:
        policy = GuardPolicy(intervene=True, confirm_s=1.0, interval_s=0.5)
        run = Run(policy)
        decisions = run.run(host(2.0, ALARM_CRITICAL), workers(4), seconds=4.0, outside_gb=40.0)
        assert not any(ActionKind.SHED in kinds(d) for d in decisions)
        assert any(ActionKind.HOLD_NOT_AT_FAULT in kinds(d) for d in decisions)
        assert any(ActionKind.BLAME in kinds(d) for d in decisions)
        assert all(d.paused for d in decisions)

    def test_blame_is_reported_once_per_episode(self) -> None:
        policy = GuardPolicy(intervene=True, confirm_s=1.0, interval_s=0.5)
        run = Run(policy)
        decisions = run.run(host(2.0, ALARM_CRITICAL), workers(4), seconds=4.0, outside_gb=40.0)
        assert sum(ActionKind.BLAME in kinds(d) for d in decisions) == 1

    def test_fault_is_reassessed_when_the_culprit_leaves(self) -> None:
        policy = GuardPolicy(intervene=True, confirm_s=1.0, interval_s=0.5)
        run = Run(policy)
        run.run(host(2.0, ALARM_CRITICAL), workers(4), seconds=2.0, outside_gb=40.0)
        later = run.run(host(2.0, ALARM_CRITICAL), workers(4), seconds=3.0, outside_gb=0.0)
        assert any(ActionKind.SHED in kinds(d) for d in later)


class TestEscalation:
    def test_spent_rounds_on_an_unhappy_but_standing_host_hold(self) -> None:
        policy = GuardPolicy(
            intervene=True, confirm_s=0.5, interval_s=0.5, max_shed_rounds=1, shed_settle_s=0.5
        )
        run = Run(policy)
        decisions = run.run(host(6.0, ALARM_CRITICAL), workers(10), seconds=5.0)
        assert sum(ActionKind.SHED in kinds(d) for d in decisions) == 1
        assert any(ActionKind.HOLD_SPENT in kinds(d) for d in decisions)
        assert not any(ActionKind.ABORT in kinds(d) for d in decisions)

    def test_failing_host_with_spent_rounds_aborts(self) -> None:
        policy = GuardPolicy(
            intervene=True, confirm_s=0.5, interval_s=0.5, max_shed_rounds=1, shed_settle_s=0.5
        )
        run = Run(policy)
        decisions = run.run(host(1.0, ALARM_CRITICAL), workers(10), seconds=5.0)
        aborts = [d for d in decisions if ActionKind.ABORT in kinds(d)]
        assert len(aborts) == 1
        assert aborts[0].state is PressureState.CATASTROPHIC
        assert run.engine.aborted

    def test_failing_host_with_no_workers_aborts_without_shedding(self) -> None:
        policy = GuardPolicy(intervene=True, confirm_s=0.5, interval_s=0.5)
        run = Run(policy)
        decisions = run.run(host(1.0, ALARM_CRITICAL), (), seconds=2.0)
        assert any(ActionKind.ABORT in kinds(d) for d in decisions)


class TestAccuracyGate:
    def test_accuracy_is_bought_near_any_unhappiness(self) -> None:
        run = Run()
        assert not run.step(host(12.0, 1)).needs_accuracy
        assert run.step(host(12.0, ALARM_WARNING)).needs_accuracy
        run2 = Run()
        assert run2.step(host(4.0, 1)).needs_accuracy  # below warn_gb
        run3 = Run()
        tight = host(12.0, 1, disk_gb=3.0, swap_total_mb=0, swap_used_mb=0)
        assert run3.step(tight).needs_accuracy  # suspension distance


class TestStates:
    def test_watch_state_when_pressure_is_moderate(self) -> None:
        run = Run()
        assert run.step(host(12.0, ALARM_WARNING)).state is PressureState.WATCH
        assert run.step(host(4.0, 1)).state is PressureState.WATCH
        assert run.step(host(12.0, 1)).state is PressureState.HEALTHY
