"""The ``safeproc`` command: thin adapters over the library services.

``watch`` observes an existing tree and journals; ``--policy guard`` enables
intervention. ``replay`` runs a journal back through the policy. ``run`` and ``status``
arrive with the broker in a later phase. Diagnostics go to stderr, machine output to
stdout, and exit codes follow the memory guard's.
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import json
import os
import signal
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import FrameType

from safeproc import __version__
from safeproc._platform.base import Provider, UnsupportedPlatformError, get_provider
from safeproc.identity import ProcessTarget, find_by_pattern
from safeproc.journal import Journal, event_record
from safeproc.models import GuardPolicy
from safeproc.monitor import ProcessMonitor, WatchOutcome
from safeproc.replay import replay_journal


def _note(message: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def _silent(_message: str) -> None:
    return None


def _version() -> str:
    return __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safeproc",
        description="Process-tree monitoring and host-safety coordination.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    watch = sub.add_parser(
        "watch",
        help="observe an existing process tree; --policy guard enables intervention",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Thresholds are host-wide reclaimable memory, never per-process. Exit codes: "
            "0 finished, 1 no match or unsupported host, 2 tree aborted, 3 --once found danger."
        ),
    )
    target = watch.add_mutually_exclusive_group(required=True)
    target.add_argument("--pid", type=int, help="root pid of the tree to monitor")
    target.add_argument("--pattern", help="argv fragment locating the root (observation only)")
    watch.add_argument(
        "--policy",
        choices=("observe", "guard"),
        default="observe",
        help="observe journals and never signals; guard pauses, sheds, and may abort",
    )
    watch.add_argument(
        "--dry-run", action="store_true", help="under guard, decide but signal nothing"
    )
    watch.add_argument("--once", action="store_true", help="take one reading, report it, exit")
    watch.add_argument(
        "--journal", metavar="PATH", help="JSONL journal (default: ./safeproc-<pid>.jsonl)"
    )
    watch.add_argument("--no-journal", action="store_true", help="write no journal")
    watch.add_argument(
        "--format", choices=("text", "json"), default="text", help="--once report format"
    )
    watch.add_argument(
        "--no-progress", action="store_true", help="suppress progress lines; implied by CI"
    )
    defaults = GuardPolicy()
    watch.add_argument(
        "--interval", type=float, default=defaults.interval_s, help="seconds between samples"
    )
    watch.add_argument(
        "--danger-gb", type=float, default=defaults.danger_gb, help="host-wide reclaimable floor"
    )
    watch.add_argument(
        "--danger-pressure",
        type=int,
        default=defaults.danger_pressure,
        help="alarm required with the floor",
    )
    watch.add_argument(
        "--warn-gb",
        type=float,
        default=defaults.warn_gb,
        help="reclaimable below which to log loudly",
    )
    watch.add_argument(
        "--reaction-window-s",
        type=float,
        default=defaults.reaction_window_s,
        help="seconds of warning before the floor",
    )
    watch.add_argument(
        "--confirm-seconds",
        type=float,
        default=defaults.confirm_s,
        help="wall-clock seconds danger must persist",
    )
    watch.add_argument(
        "--compressor-rate-gbs",
        type=float,
        default=defaults.compressor_rate_gbs,
        help="compressor growth that is predictive danger",
    )
    watch.add_argument(
        "--stall-full-pct",
        type=float,
        default=defaults.stall_full_pct,
        help="Linux full-stall share that is measured danger",
    )
    watch.add_argument(
        "--min-run-s", type=float, default=defaults.min_run_s, help="service window between pauses"
    )
    watch.add_argument(
        "--max-pause-s", type=float, default=defaults.max_pause_s, help="hard cap on a pause"
    )
    watch.add_argument(
        "--snapshot-interval",
        type=float,
        default=defaults.snapshot_interval_s,
        help="seconds between journal snapshots",
    )
    watch.add_argument(
        "--heartbeat-lag-s",
        type=float,
        default=defaults.heartbeat_lag_s,
        help="lag that counts as starvation; diagnostic only",
    )
    watch.add_argument(
        "--pool-limit",
        type=int,
        default=None,
        metavar="N",
        help="shed when more than N shed-able workers exist",
    )
    watch.add_argument(
        "--min-worker-mb",
        type=float,
        default=defaults.min_worker_mb,
        help="ignore smaller processes when shedding",
    )
    watch.add_argument(
        "--worker-pattern",
        action="append",
        metavar="TEXT",
        help="only shed processes whose argv contains this; repeatable",
    )
    watch.add_argument(
        "--term-grace-s",
        type=float,
        default=defaults.term_grace_s,
        help="SIGTERM grace before SIGKILL",
    )
    watch.add_argument(
        "--shed-fraction",
        type=float,
        default=defaults.shed_fraction,
        help="fraction of tree memory per round",
    )
    watch.add_argument(
        "--shed-settle-s",
        type=float,
        default=defaults.shed_settle_s,
        help="earliest next round after one",
    )
    watch.add_argument(
        "--max-shed-rounds",
        type=int,
        default=defaults.max_shed_rounds,
        help="rounds before hold-or-abort",
    )

    replay = sub.add_parser("replay", help="run a journal back through the policy")
    replay.add_argument("journal", type=Path)
    replay.add_argument("--format", choices=("text", "json"), default="text")
    replay.add_argument(
        "--show-mismatches", action="store_true", help="list samples whose replayed actions differ"
    )
    return parser


def policy_from_args(args: argparse.Namespace) -> GuardPolicy:
    return GuardPolicy(
        intervene=args.policy == "guard",
        dry_run=bool(args.dry_run),
        danger_gb=args.danger_gb,
        danger_pressure=args.danger_pressure,
        warn_gb=args.warn_gb,
        reaction_window_s=args.reaction_window_s,
        confirm_s=args.confirm_seconds,
        compressor_rate_gbs=args.compressor_rate_gbs,
        stall_full_pct=args.stall_full_pct,
        pool_limit=args.pool_limit,
        min_worker_mb=args.min_worker_mb,
        shed_fraction=args.shed_fraction,
        shed_settle_s=args.shed_settle_s,
        min_run_s=args.min_run_s,
        max_pause_s=args.max_pause_s,
        heartbeat_lag_s=args.heartbeat_lag_s,
        max_shed_rounds=args.max_shed_rounds,
        term_grace_s=args.term_grace_s,
        interval_s=args.interval,
        snapshot_interval_s=args.snapshot_interval,
        worker_patterns=tuple(args.worker_pattern or ()),
    )


def _resolve_target(args: argparse.Namespace, provider: Provider) -> ProcessTarget | None:
    if args.pid is not None:
        return ProcessTarget(pid=int(args.pid))
    pattern = str(args.pattern)
    table = provider.discovery_table()
    row = find_by_pattern(pattern, table, exclude_pids=(os.getpid(), os.getppid()))
    if row is None:
        return None
    return ProcessTarget(pid=row.pid, create_token=row.create_token, label=pattern)


def _install_resume_guarantees(monitor: ProcessMonitor) -> None:
    """Process-level guarantees that a paused producer is resumed: atexit and signals.

    These belong to the application, not the library; a library that replaced a caller's
    handlers would break the concurrent-instances contract.
    """

    def resume() -> None:
        handle = monitor.handle
        if handle is not None and not handle.stop_requested:
            handle.stop()

    atexit.register(resume)
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(sig)

        def handler(signum: int, frame: FrameType | None, _prev: object = previous) -> None:
            resume()
            if callable(_prev):
                _prev(signum, frame)
            else:
                raise SystemExit(128 + signum)

        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, handler)


def _report_once(monitor: ProcessMonitor, fmt: str) -> None:
    handle = monitor.handle
    if handle is None or handle.last_host is None or handle.last_tree is None:
        return
    host, tree, decision = handle.last_host, handle.last_tree, handle.last_decision
    danger = decision is not None and decision.measured
    if fmt == "json":
        print(
            json.dumps(
                {
                    "record": "once",
                    "target_pid": handle.identity.pid,
                    "danger": danger,
                    "reason": None
                    if decision is None or decision.reason is None
                    else str(decision.reason),
                    "state": None if decision is None else str(decision.state),
                    "host": {
                        "reclaimable_gb": round(host.reclaimable_gb, 3),
                        "free_gb": round(host.free_gb, 3),
                        "pressure": host.pressure,
                        "compressed_gb": round(host.compressed_gb, 3),
                        "swap_used_mb": round(host.swap_used_mb, 1),
                        "suspension_gb": round(host.suspension_gb, 2),
                        "stall_some_pct": host.stall_some_pct,
                        "stall_full_pct": host.stall_full_pct,
                    },
                    "tree": {
                        "procs": tree.procs,
                        "workers": tree.workers,
                        "cost_gb": round(tree.cost_gb, 3),
                        "measured": tree.measured,
                    },
                    "machine": dict(monitor.provider.machine_facts()),
                },
                separators=(",", ":"),
                default=str,
            )
        )
        return
    print(
        f"reclaimable {host.reclaimable_gb:.2f} GB   free {host.free_gb:.2f}   "
        f"compressed {host.compressed_gb:.2f}   swap {host.swap_used_mb:.0f} MB   "
        f"alarm {host.pressure}"
    )
    print(
        f"tree {tree.cost_gb:.2f} GB ({'measured' if tree.measured else 'RSS'})   "
        f"procs {tree.procs}   shed-able {tree.workers}"
    )
    if host.stall_some_pct is not None:
        print(f"stall some {host.stall_some_pct}%   full {host.stall_full_pct}%")
    if host.disk_gb < 999.0:
        print(f"suspension distance {host.suspension_gb:.1f} GB   disk free {host.disk_gb:.1f} GB")
    state = "?" if decision is None else str(decision.state)
    print(f"state {state}   danger: {'YES' if danger else 'no'}")


def cmd_watch(args: argparse.Namespace) -> int:
    try:
        provider = get_provider()
    except UnsupportedPlatformError as exc:
        _note(str(exc))
        return int(WatchOutcome.NO_MATCH)
    policy = policy_from_args(args)
    quiet = bool(args.no_progress) or bool(os.environ.get("CI"))
    notify = _silent if quiet else _note
    target = _resolve_target(args, provider)
    if target is None:
        _note("no process matched; nothing to monitor")
        return int(WatchOutcome.NO_MATCH)
    _note(f"scheduling: {provider.harden_scheduling()}")

    journal_path: Path | None = None
    if not args.no_journal and not args.once:
        journal_path = Path(args.journal) if args.journal else Path(f"safeproc-{target.pid}.jsonl")

    def run_with(journal: Journal | None) -> int:
        monitor = ProcessMonitor(
            target,
            provider=provider,
            policy=policy,
            journal=journal,
            notify=notify,
            once=bool(args.once),
        )
        if policy.intervene and not policy.dry_run:
            _install_resume_guarantees(monitor)
        try:
            outcome = monitor.run()
        except KeyboardInterrupt:
            return int(WatchOutcome.FINISHED)
        if args.once:
            _report_once(monitor, str(args.format))
        return int(outcome)

    if journal_path is None:
        return run_with(None)
    with journal_path.open("a", buffering=1, encoding="utf-8") as handle:
        journal = Journal(handle)
        journal.write(event_record("scheduling", {"result": provider.harden_scheduling()}))
        _note(f"journaling to {journal_path}")
        return run_with(journal)


def cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.journal)
    if not path.exists():
        _note(f"no such journal: {path}")
        return 1
    result = replay_journal(path)
    summary = result.as_dict()
    if args.format == "json":
        payload: dict[str, object] = {"journal": str(path), **summary}
        if args.show_mismatches:
            payload["mismatch_samples"] = [
                {
                    "t": step.t,
                    "recorded": list(step.recorded_actions),
                    "replayed": list(step.replayed_actions),
                }
                for step in result.mismatches
            ]
        print(json.dumps(payload, separators=(",", ":")))
    else:
        print(f"journal {path}: {summary['samples']} samples, {summary['skipped']} skipped")
        print(
            f"pauses {summary['pauses']}  resumes {summary['resumes']}  sheds {summary['sheds']}  "
            f"aborts {summary['aborts']}  predictive holds {summary['predictive_holds']}  "
            f"not-at-fault holds {summary['holds_not_at_fault']}"
        )
        print(f"max state {summary['max_state']}  late heartbeats {summary['late_heartbeats']}")
        print(f"mismatches against recorded actions: {summary['mismatches']}")
        if args.show_mismatches:
            for step in result.mismatches:
                print(
                    f"  t={step.t}: recorded {list(step.recorded_actions)} replayed {list(step.replayed_actions)}"
                )
    return 0 if not result.mismatches else 4


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "watch":
        return cmd_watch(args)
    if args.command == "replay":
        return cmd_replay(args)
    return 2  # pragma: no cover - argparse rejects unknown commands


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
