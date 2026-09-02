"""The monitor against a scripted provider: observation never signals, guard does."""

from __future__ import annotations

import io
import signal

from safeproc.clocks import FakeClock
from safeproc.identity import ProcessTarget
from safeproc.journal import Journal, iter_records
from safeproc.models import ALARM_CRITICAL, GuardPolicy
from safeproc.monitor import ProcessMonitor, ProducerPause, WatchOutcome, terminate_batch
from tests.conftest import FakeProvider, host, row


def _monitor(
    provider: FakeProvider,
    clock: FakeClock,
    policy: GuardPolicy,
    *,
    journal: Journal | None = None,
    once: bool = False,
    max_samples: int = 40,
) -> ProcessMonitor:
    count = 0

    def sleep(seconds: float) -> None:
        nonlocal count
        count += 1
        clock.advance(seconds)
        handle = monitor.handle
        if count >= max_samples and handle is not None:
            handle.stop()

    monitor = ProcessMonitor(
        ProcessTarget(pid=100),
        provider=provider,
        policy=policy,
        clock=clock,
        journal=journal,
        sleep=sleep,
        once=once,
    )
    return monitor


def test_no_match_when_the_pid_is_absent(
    fake_provider: FakeProvider, fake_clock: FakeClock
) -> None:
    monitor = ProcessMonitor(ProcessTarget(pid=4242), provider=fake_provider, clock=fake_clock)
    assert monitor.run() is WatchOutcome.NO_MATCH


def test_token_mismatch_is_no_match(fake_provider: FakeProvider, fake_clock: FakeClock) -> None:
    monitor = ProcessMonitor(
        ProcessTarget(pid=100, create_token=1), provider=fake_provider, clock=fake_clock
    )
    assert monitor.run() is WatchOutcome.NO_MATCH


def test_observation_never_signals_even_in_danger(
    fake_provider: FakeProvider, fake_clock: FakeClock
) -> None:
    fake_provider.hosts = [host(1.0, ALARM_CRITICAL)]
    buffer = io.StringIO()
    policy = GuardPolicy(intervene=False, confirm_s=0.5, interval_s=0.5)
    monitor = _monitor(fake_provider, fake_clock, policy, journal=Journal(buffer), max_samples=12)
    assert monitor.run() is WatchOutcome.FINISHED
    assert fake_provider.signals == []
    records = list(iter_records(buffer.getvalue().splitlines()))
    kinds = [r.kind for r in records]
    assert kinds[0] == "session"
    assert kinds[-1] == "summary"
    events = [r for r in records if r.kind == "event"]
    assert any(r.payload.get("kind") == "pause" and r.payload.get("observed_only") for r in events)
    assert any(r.payload.get("kind") == "shed" for r in events), (
        "observation still journals what guard would do"
    )


def test_guard_pauses_every_spawner_and_resumes_on_exit(
    fake_provider: FakeProvider, fake_clock: FakeClock
) -> None:
    fake_provider.hosts = [host(2.5, ALARM_CRITICAL)]
    policy = GuardPolicy(intervene=True, confirm_s=100.0, interval_s=0.5, max_pause_s=100.0)
    monitor = _monitor(fake_provider, fake_clock, policy, max_samples=3)
    assert monitor.run() is WatchOutcome.FINISHED
    stopped = fake_provider.sent(signal.SIGSTOP)
    assert set(stopped) == {100, 101, 106}, "root and every intermediate spawner, not the leaves"
    assert 102 not in stopped and 107 not in stopped
    continued = fake_provider.sent(signal.SIGCONT)
    assert set(continued) == {100, 101, 106}, "resume is guaranteed on exit"


def test_guard_sheds_the_largest_worker_subtree(
    fake_provider: FakeProvider, fake_clock: FakeClock
) -> None:
    fake_provider.hosts = [host(2.5, ALARM_CRITICAL)]
    policy = GuardPolicy(
        intervene=True, confirm_s=1.0, interval_s=0.5, shed_settle_s=100.0, max_pause_s=100.0
    )
    monitor = _monitor(fake_provider, fake_clock, policy, max_samples=6)
    monitor.run()
    terms = fake_provider.sent(signal.SIGTERM)
    assert 107 in terms, "the 2.5 GB worker is the proportional victim"
    assert 102 not in terms and 103 not in terms
    handle = monitor.handle
    assert handle is not None
    interventions = handle.summary["interventions"]
    assert isinstance(interventions, dict)
    assert interventions["processes_killed"] == 1


def test_dry_run_decides_but_signals_nothing(
    fake_provider: FakeProvider, fake_clock: FakeClock
) -> None:
    fake_provider.hosts = [host(2.5, ALARM_CRITICAL)]
    buffer = io.StringIO()
    policy = GuardPolicy(intervene=True, dry_run=True, confirm_s=1.0, interval_s=0.5)
    monitor = _monitor(fake_provider, fake_clock, policy, journal=Journal(buffer), max_samples=6)
    monitor.run()
    assert fake_provider.signals == []
    events = [r for r in iter_records(buffer.getvalue().splitlines()) if r.kind == "event"]
    assert any(r.payload.get("kind") == "shed" and r.payload.get("dry_run") for r in events)


def test_abort_terminates_the_root_and_reports_aborted(
    fake_provider: FakeProvider, fake_clock: FakeClock
) -> None:
    fake_provider.hosts = [host(0.5, ALARM_CRITICAL)]
    policy = GuardPolicy(intervene=True, confirm_s=0.5, interval_s=0.5, max_shed_rounds=0)
    monitor = _monitor(fake_provider, fake_clock, policy, max_samples=10)
    assert monitor.run() is WatchOutcome.ABORTED
    assert 100 in fake_provider.sent(signal.SIGTERM)


def test_once_reports_danger(fake_provider: FakeProvider, fake_clock: FakeClock) -> None:
    fake_provider.hosts = [host(1.0, ALARM_CRITICAL)]
    monitor = _monitor(fake_provider, fake_clock, GuardPolicy(), once=True)
    assert monitor.run() is WatchOutcome.DANGER
    handle = monitor.handle
    assert handle is not None and handle.last_tree is not None
    assert handle.last_tree.measured
    fake_provider.hosts = [host(12.0, 1)]
    assert (
        _monitor(fake_provider, fake_clock, GuardPolicy(), once=True).run() is WatchOutcome.FINISHED
    )


def test_monitor_stops_when_the_pid_is_recycled(
    fake_provider: FakeProvider, fake_clock: FakeClock
) -> None:
    calls = 0
    original = fake_provider.process_table

    def table_then_recycle() -> list:  # type: ignore[type-arg]
        nonlocal calls
        calls += 1
        rows = original()
        if calls > 2:
            rows = [row(100, 1, token=7) if r.pid == 100 else r for r in rows]
        return rows

    fake_provider.process_table = table_then_recycle  # type: ignore[method-assign]
    monitor = _monitor(fake_provider, fake_clock, GuardPolicy(), max_samples=20)
    assert monitor.run() is WatchOutcome.FINISHED
    handle = monitor.handle
    assert handle is not None and handle.samples == 1


def test_producer_pause_refreshes_newborn_spawners(fake_provider: FakeProvider) -> None:
    pause = ProducerPause(100, fake_provider)
    assert pause.pause(fake_provider.table) == 3
    fake_provider.table.append(row(108, 101, cmd="new orchestrator"))
    fake_provider.table.append(row(109, 108, rss_mb=800, cmd="new worker"))
    pause.refresh(fake_provider.table)
    assert 108 in fake_provider.sent(signal.SIGSTOP)
    assert 109 not in fake_provider.sent(signal.SIGSTOP)
    assert pause.resume() == 4
    assert pause.resume() == 0


def test_terminate_batch_walks_deepest_first_and_shares_one_grace(
    fake_provider: FakeProvider,
) -> None:
    fake_provider.kill_on_term = False
    slept: list[float] = []
    outcome = terminate_batch(
        [106], provider=fake_provider, table=fake_provider.table, grace_s=0.0, sleep=slept.append
    )
    assert outcome == {106: "kill"}
    order = [pid for pid, _sig in fake_provider.signals]
    assert order[:2] == [106, 107], "stop the root, then SIGTERM the deepest member first"
    assert (106, signal.SIGCONT) in fake_provider.signals
    assert (107, signal.SIGKILL) in fake_provider.signals
