"""Monitoring an existing process tree: observe by default, intervene by explicit policy.

``ProcessMonitor`` fences a target by identity, samples the host and the tree, runs the
pressure engine, and, only under an intervention policy, applies its decisions through
the platform provider. It is a sidecar, not a parent: it did not spawn the tree, cannot
trust the inherited process group, and walks the tree explicitly when it must act.

Almost every awkward mechanism here follows from not owning the spawn. The owned launch
path in a later phase does better because it can.
"""

from __future__ import annotations

import signal
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

from safeproc._platform.base import Provider
from safeproc.clocks import ActiveClock, Clock
from safeproc.identity import (
    ProcessIdentity,
    ProcessRecord,
    ProcessTarget,
    deepest_first,
    descendants,
    fenced,
    spawners,
)
from safeproc.journal import (
    Journal,
    Tally,
    event_record,
    sample_record,
    session_record,
    snapshot_record,
    summary_record,
)
from safeproc.models import (
    Action,
    ActionKind,
    Candidate,
    Decision,
    GuardPolicy,
    HostSample,
    OutsideReading,
    SupervisionMode,
    TreeSample,
)
from safeproc.policy import PressureEngine

Notify = Callable[[str], None]
Sleep = Callable[[float], None]


class WatchOutcome(IntEnum):
    """Exit status of a watch, as the CLI reports it."""

    FINISHED = 0
    NO_MATCH = 1
    ABORTED = 2
    DANGER = 3


def _quiet(_: str) -> None:
    return None


class ProducerPause:
    """Hold every spawner in the tree stopped, so nothing new arrives mid-harvest.

    A stopped root that is never resumed is worse than the crash this prevents, so the
    monitor resumes in its ``finally`` and the CLI adds process-level guarantees. Pause
    and resume are idempotent in both directions.
    """

    def __init__(self, root_pid: int, provider: Provider, notify: Notify = _quiet) -> None:
        self.root_pid = root_pid
        self._provider = provider
        self._notify = notify
        self.paused = False
        self.stopped: set[int] = set()

    def pause(self, table: Sequence[ProcessRecord]) -> int:
        """Freeze every spawner not already stopped. Returns how many were newly stopped."""
        tree = descendants(self.root_pid, table)
        newly = [
            pid
            for pid in spawners(self.root_pid, tree)
            if pid not in self.stopped and self._provider.signal(pid, signal.SIGSTOP)
        ]
        self.stopped.update(newly)
        if not self.paused and self.stopped:
            self.paused = True
            self._notify(f"PAUSE {len(self.stopped)} spawner(s) under pid={self.root_pid}")
        elif newly:
            self._notify(f"PAUSE extended to {len(newly)} new spawner(s)")
        return len(newly)

    def refresh(self, table: Sequence[ProcessRecord]) -> None:
        """Re-freeze intermediates born since the pause. Idempotent."""
        if self.paused:
            self.pause(table)

    def resume(self) -> int:
        """``SIGCONT`` every stopped spawner. Safe from ``finally`` and signal handlers."""
        if not self.paused and not self.stopped:
            return 0
        self.paused = False
        count = 0
        for pid in sorted(self.stopped):
            if self._provider.signal(pid, signal.SIGCONT):
                count += 1
        self.stopped.clear()
        self._notify(f"RESUME {count} spawner(s) under pid={self.root_pid}")
        return count


def terminate_batch(
    pids: Sequence[int],
    *,
    provider: Provider,
    table: Sequence[ProcessRecord],
    grace_s: float,
    sleep: Sleep = time.sleep,
) -> dict[int, str]:
    """End several subtrees, paying the ``SIGTERM`` grace once for the lot.

    Each victim root is stopped before its descendants are enumerated, because a running
    parent forks faster than the walk. Members are signalled deepest-first so no kill
    orphans a process a later kill needs, and the root is resumed after ``SIGTERM`` so a
    stopped process can handle it. Returns each root's disposition: ``gone``, ``term``,
    ``kill``, or ``denied``.
    """
    outcome: dict[int, str] = {}
    plans: list[tuple[int, list[int]]] = []
    for pid in pids:
        provider.signal(pid, signal.SIGSTOP)
        plans.append((pid, deepest_first(pid, table)))
    for pid, subtree in plans:
        for member in subtree:
            provider.signal(member, signal.SIGTERM)
        if provider.signal(pid, signal.SIGTERM):
            provider.signal(pid, signal.SIGCONT)
        else:
            for member in subtree:
                provider.signal(member, signal.SIGKILL)
            provider.signal(pid, signal.SIGCONT)
            outcome[pid] = "gone"
    pending = [(pid, subtree) for pid, subtree in plans if pid not in outcome]
    if pending:
        everyone = [member for pid, subtree in pending for member in (pid, *subtree)]
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline and any(provider.alive(pid) for pid in everyone):
            sleep(0.25)
        for pid, subtree in pending:
            if any(provider.alive(member) for member in (pid, *subtree)):
                for member in subtree:
                    provider.signal(member, signal.SIGKILL)
                provider.signal(pid, signal.SIGKILL)
                outcome[pid] = "kill"
            else:
                outcome[pid] = "term"
    return outcome


@dataclass
class MonitoredProcess:
    """The fenced existing target and what the monitor has seen of it.

    The name states monitoring rather than attachment: the runtime does not use
    ``ptrace``, become the target's parent, or prevent it from exiting.
    """

    target: ProcessTarget
    identity: ProcessIdentity
    mode: SupervisionMode = SupervisionMode.MONITORED
    samples: int = 0
    last_host: HostSample | None = None
    last_tree: TreeSample | None = None
    last_decision: Decision | None = None
    outcome: WatchOutcome | None = None
    summary: dict[str, object] = field(default_factory=dict)
    _stop_requested: bool = False

    def stop(self) -> None:
        """Ask the watch loop to finish after the current sample."""
        self._stop_requested = True

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested


class ProcessMonitor:
    """Observe an existing tree, and intervene only under an explicit policy."""

    def __init__(
        self,
        target: ProcessTarget,
        *,
        provider: Provider,
        policy: GuardPolicy | None = None,
        clock: Clock | None = None,
        journal: Journal | None = None,
        sleep: Sleep = time.sleep,
        notify: Notify = _quiet,
        once: bool = False,
    ) -> None:
        self.target = target
        self.provider = provider
        self.policy = policy or GuardPolicy()
        self.clock: Clock = clock or ActiveClock()
        self.journal = journal
        self.sleep = sleep
        self.notify = notify
        self.once = once
        self.engine = PressureEngine(self.policy)
        self.handle: MonitoredProcess | None = None

    # ── discovery ────────────────────────────────────────────────────────────

    def locate(self) -> ProcessRecord | None:
        """The live root for the target, fenced by token when the target carries one."""
        table = self.provider.process_table()
        for row in table:
            if row.pid != self.target.pid or row.is_zombie:
                continue
            if (
                self.target.create_token is not None
                and row.create_token != self.target.create_token
            ):
                return None
            return row
        return None

    # ── the loop ─────────────────────────────────────────────────────────────

    def run(self) -> WatchOutcome:
        root = self.locate()
        if root is None:
            self.notify("no process matched; nothing to monitor")
            return WatchOutcome.NO_MATCH
        handle = MonitoredProcess(self.target, root.identity)
        self.handle = handle
        policy = self.policy
        journal = self.journal
        started = self.clock.now()
        tally = Tally(started=started)
        pause = ProducerPause(root.pid, self.provider, self.notify)
        last_snapshot = started
        outside_cache: OutsideReading | None = None

        if journal is not None:
            journal.write(
                session_record(
                    target_pid=root.pid,
                    target_cmd=root.cmd,
                    mode=SupervisionMode.MONITORED,
                    policy=policy,
                    machine=self.provider.machine_facts(),
                    capabilities=self.provider.capabilities().as_dict(),
                )
            )
        self.notify(
            f"monitoring pid={root.pid} ({'guard' if policy.intervene else 'observe'}"
            f"{', dry run' if policy.dry_run else ''}): floor {policy.danger_gb} GB at "
            f"alarm {policy.danger_pressure}, confirm {policy.confirm_s}s"
        )

        outcome = WatchOutcome.FINISHED
        try:
            while not handle.stop_requested:
                table = self.provider.process_table()
                live = fenced(handle.identity, table)
                if live is None:
                    self.notify("monitored process exited")
                    break
                now = self.clock.now()
                tree_rows = descendants(root.pid, table)
                accurate = self.once or self.engine.needs_accuracy()
                if accurate:
                    measured = self.provider.costs(
                        [row.pid for row in tree_rows], policy.min_worker_mb
                    )
                    tree_rows = [
                        ProcessRecord(
                            pid=row.pid,
                            ppid=row.ppid,
                            uid=row.uid,
                            state=row.state,
                            rss_mb=row.rss_mb,
                            age_s=row.age_s,
                            cmd=row.cmd,
                            create_token=row.create_token,
                            footprint_mb=measured.get(row.pid, 0.0),
                        )
                        for row in tree_rows
                    ]
                workers = [
                    row
                    for row in tree_rows
                    if row.pid != root.pid
                    and row.cost_mb >= policy.min_worker_mb
                    and (
                        not policy.worker_patterns
                        or any(token in row.cmd for token in policy.worker_patterns)
                    )
                ]
                candidates = [
                    Candidate(pid=row.pid, cost_mb=row.cost_mb, age_s=row.age_s, cmd=row.cmd)
                    for row in workers
                ]
                host = self.provider.host_sample()
                tree = TreeSample(
                    procs=len(tree_rows),
                    workers=len(workers),
                    cost_gb=sum(row.cost_mb for row in tree_rows) / 1024,
                    rss_gb=sum(row.rss_mb for row in tree_rows) / 1024,
                    measured=accurate,
                    worker_cost_mb=tuple(
                        sorted((round(row.cost_mb) for row in workers), reverse=True)
                    ),
                )

                outside_cache = None

                def outside(
                    _table: Sequence[ProcessRecord] = table,
                    _tree: Sequence[ProcessRecord] = tree_rows,
                ) -> OutsideReading:
                    nonlocal outside_cache
                    if outside_cache is None:
                        outside_cache = self._outside(_table, _tree)
                    return outside_cache

                decision = self.engine.evaluate(now, host, tree, candidates, outside)
                cadence = self.engine.last_cadence
                tally.observe(host, tree, cadence.lag_s)
                handle.samples += 1
                handle.last_host = host
                handle.last_tree = tree
                handle.last_decision = decision

                aborted = self._apply(decision, table, root, workers, pause, tally)
                if policy.intervene and not policy.dry_run:
                    pause.refresh(table)

                if journal is not None:
                    journal.write(
                        sample_record(
                            t=now,
                            host=host,
                            tree=tree,
                            workers=candidates,
                            cadence=cadence,
                            decision=decision,
                            outside_gb=None if outside_cache is None else outside_cache.total_gb,
                        )
                    )
                    if now - last_snapshot >= policy.snapshot_interval_s:
                        top = sorted(workers, key=lambda row: -row.cost_mb)[:8]
                        journal.write(
                            snapshot_record(
                                elapsed_s=now - started,
                                host=host,
                                tree=tree,
                                top=[(row.pid, row.cost_mb, row.age_s, row.cmd) for row in top],
                            )
                        )
                        last_snapshot = now
                self._progress(decision, host, tree)

                if aborted:
                    outcome = WatchOutcome.ABORTED
                    break
                if self.once:
                    outcome = WatchOutcome.DANGER if decision.measured else WatchOutcome.FINISHED
                    break
                self.sleep(policy.interval_s)
        finally:
            # The one guarantee that matters more than any threshold.
            pause.resume()
            now = self.clock.now()
            handle.summary = tally.summary(
                now,
                pauses=self.engine.pauses,
                paused_total_s=self.engine.paused_total_s(now),
                late_heartbeats=self.engine.late_heartbeats,
            )
            if journal is not None:
                journal.write(summary_record(handle.summary))
        handle.outcome = outcome
        return outcome

    # ── pieces ───────────────────────────────────────────────────────────────

    def _outside(
        self, table: Sequence[ProcessRecord], tree: Sequence[ProcessRecord]
    ) -> OutsideReading:
        """Memory held by this user's processes outside the tree, recomputed each call."""
        uid = self.provider.current_uid()
        inside = {row.pid for row in tree}
        others = [
            row.pid
            for row in table
            if row.uid == uid and row.pid not in inside and not row.is_zombie
        ]
        top = self.provider.costs(others, max(512.0, self.policy.min_worker_mb))
        return OutsideReading(sum(top.values()) / 1024, top)

    def _apply(
        self,
        decision: Decision,
        table: Sequence[ProcessRecord],
        root: ProcessRecord,
        workers: Sequence[ProcessRecord],
        pause: ProducerPause,
        tally: Tally,
    ) -> bool:
        """Journal every action; signal only under an intervention policy. Returns abort."""
        policy = self.policy
        by_pid = {row.pid: row for row in workers}
        aborted = False
        for action in decision.actions:
            self._journal_action(action)
            if not policy.intervene:
                continue
            match action.kind:
                case ActionKind.PAUSE:
                    if not policy.dry_run:
                        pause.pause(table)
                    else:
                        self.notify("WOULD PAUSE spawners")
                case ActionKind.RESUME:
                    if not policy.dry_run:
                        pause.resume()
                    else:
                        self.notify("WOULD RESUME spawners")
                case ActionKind.SHED:
                    victims = [by_pid[pid] for pid in action.pids if pid in by_pid]
                    for victim in victims:
                        self.notify(
                            f"{'WOULD ' if policy.dry_run else ''}SHED pid={victim.pid} "
                            f"cost={victim.cost_mb / 1024:.1f}GB age={victim.age_s:.0f}s"
                        )
                    if policy.dry_run:
                        continue
                    outcome = terminate_batch(
                        [victim.pid for victim in victims],
                        provider=self.provider,
                        table=table,
                        grace_s=policy.term_grace_s,
                        sleep=self.sleep,
                    )
                    for victim in victims:
                        tally.note_disposition(outcome.get(victim.pid, "gone"))
                    tally.note_harvest(
                        [Candidate(v.pid, v.cost_mb, v.age_s, v.cmd) for v in victims]
                    )
                    self.engine.rebase_cadence(self.clock.now())
                case ActionKind.ABORT:
                    self.notify(
                        f"{'WOULD ' if policy.dry_run else ''}ABORT pid={root.pid}: shedding "
                        "did not restore the host"
                    )
                    tally.aborted = True
                    if policy.dry_run:
                        continue
                    outcome = terminate_batch(
                        [root.pid],
                        provider=self.provider,
                        table=table,
                        grace_s=policy.term_grace_s,
                        sleep=self.sleep,
                    )
                    tally.note_disposition(outcome.get(root.pid, "gone"))
                    aborted = True
                case _:
                    pass
        return aborted

    def _journal_action(self, action: Action) -> None:
        detail: dict[str, object] = dict(action.detail)
        if action.pids:
            detail["pids"] = list(action.pids)
        if not self.policy.intervene:
            detail["observed_only"] = True
        elif self.policy.dry_run and action.kind in {
            ActionKind.PAUSE,
            ActionKind.RESUME,
            ActionKind.SHED,
            ActionKind.ABORT,
        }:
            detail["dry_run"] = True
        if self.journal is not None:
            self.journal.write(event_record(str(action.kind), detail))
        if action.kind is ActionKind.HEARTBEAT_LATE:
            self.notify(f"WARNING heartbeat late by {detail.get('lag_s')}s; this reading is stale")
        elif action.kind is ActionKind.PREDICTIVE_HOLD:
            self.notify(f"predictive danger ({detail.get('reason')}): producer held, nothing shed")
        elif action.kind is ActionKind.HOLD_NOT_AT_FAULT:
            self.notify(
                f"holding: {detail.get('outside_gb')}GB outside the tree against "
                f"{detail.get('tree_cost_gb')}GB inside; the tree is not at fault"
            )
        elif action.kind is ActionKind.BLAME:
            self.notify(
                f"host at {detail.get('reclaimable_gb')}GB reclaimable; tree "
                f"{detail.get('tree_cost_gb')}GB, outside {detail.get('outside_gb')}GB"
            )

    def _progress(self, decision: Decision, host: HostSample, tree: TreeSample) -> None:
        if (
            decision.reason is None
            and host.pressure < 2
            and host.reclaimable_gb >= self.policy.warn_gb
        ):
            return
        self.notify(
            f"{decision.state}: reclaimable {host.reclaimable_gb:.2f}GB alarm {host.pressure} "
            f"tree {tree.cost_gb:.1f}GB procs {tree.procs} workers {tree.workers}"
            + (f" ({decision.reason})" if decision.reason else "")
        )
