"""The journal: one JSON record per line, versioned, redacted, and replayable.

Five record kinds and no more; a reader should learn the whole schema from this module.

    session   once at start   what is watched, on what machine, with what policy
    sample    every interval  one compact reading of host, tree, and the monitor's cadence
    snapshot  periodically    a structured view with worker percentiles and top consumers
    event     when it happens something the monitor did or noticed, tagged by kind
    summary   once at end     rollups, distributions, and tallies

Every sample carries the engine's clock value, the full host reading, and the shed-able
candidates, which is exactly what replay needs to reproduce the decision sequence. Command
lines are truncated and environments never enter the journal.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

from safeproc.models import (
    Candidate,
    Decision,
    GuardPolicy,
    HostSample,
    PlatformName,
    SupervisionMode,
    TreeSample,
)
from safeproc.policy import Cadence

JOURNAL_SCHEMA = "safeproc.journal/1"
"""Versioned independently of the package. Bump the major on an incompatible change."""

CMD_MAX = 200


@dataclass(frozen=True)
class JournalRecord:
    """One journal line, decoded."""

    kind: str
    ts: str
    payload: Mapping[str, object]

    def as_line(self) -> str:
        body: dict[str, object] = {"record": self.kind, "ts": self.ts, **self.payload}
        return json.dumps(body, separators=(",", ":"), default=str) + "\n"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def percentiles(values: Sequence[float], points: Sequence[int] = (50, 90, 99)) -> dict[str, float]:
    """Nearest-rank percentiles. Small samples and empty input both answer sensibly."""
    if not values:
        return {f"p{p}": 0.0 for p in points}
    ordered = sorted(values)
    out: dict[str, float] = {}
    for point in points:
        rank = max(1, math.ceil(point / 100 * len(ordered)))
        out[f"p{point}"] = round(ordered[min(rank, len(ordered)) - 1], 2)
    return out


def host_payload(host: HostSample) -> dict[str, object]:
    return {
        "platform": str(host.platform),
        "reclaimable_gb": round(host.reclaimable_gb, 3),
        "free_gb": round(host.free_gb, 3),
        "wired_gb": round(host.wired_gb, 3),
        "compressed_gb": round(host.compressed_gb, 3),
        "swap_used_mb": round(host.swap_used_mb, 1),
        "swap_total_mb": round(host.swap_total_mb, 1),
        "disk_gb": round(host.disk_gb, 2),
        "pressure": host.pressure,
        "ancm_ratio": round(host.ancm_ratio, 3),
        "suspension_gb": round(host.suspension_gb, 2),
        "total_gb": None if host.total_gb is None else round(host.total_gb, 2),
        "cgroup_headroom_gb": (
            None if host.cgroup_headroom_gb is None else round(host.cgroup_headroom_gb, 3)
        ),
        "stall_some_pct": host.stall_some_pct,
        "stall_full_pct": host.stall_full_pct,
        "swapin_rate_per_s": host.swapin_rate_per_s,
    }


def host_from_payload(payload: Mapping[str, object]) -> HostSample:
    """Rebuild a host sample from a journal line. Missing optional fields stay neutral."""

    def num(key: str, default: float) -> float:
        value = payload.get(key)
        return float(value) if isinstance(value, (int, float)) else default

    def opt(key: str) -> float | None:
        value = payload.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    platform_value = payload.get("platform")
    platform = (
        PlatformName(platform_value)
        if isinstance(platform_value, str) and platform_value in PlatformName.__members__.values()
        else PlatformName.FAKE
    )
    return HostSample(
        platform=platform,
        reclaimable_gb=num("reclaimable_gb", 0.0),
        free_gb=num("free_gb", 0.0),
        pressure=int(num("pressure", 1)),
        wired_gb=num("wired_gb", 0.0),
        compressed_gb=num("compressed_gb", 0.0),
        swap_used_mb=num("swap_used_mb", 0.0),
        swap_total_mb=num("swap_total_mb", 0.0),
        disk_gb=num("disk_gb", 999.0),
        ancm_ratio=num("ancm_ratio", 1.0),
        total_gb=opt("total_gb"),
        cgroup_headroom_gb=opt("cgroup_headroom_gb"),
        stall_some_pct=opt("stall_some_pct"),
        stall_full_pct=opt("stall_full_pct"),
        swapin_rate_per_s=opt("swapin_rate_per_s"),
    )


def tree_payload(tree: TreeSample) -> dict[str, object]:
    return {
        "procs": tree.procs,
        "workers": tree.workers,
        "cost_gb": round(tree.cost_gb, 3),
        "rss_gb": round(tree.rss_gb, 3),
        "measured": tree.measured,
        "worker_cost_mb": [round(mb, 1) for mb in tree.worker_cost_mb],
    }


def tree_from_payload(payload: Mapping[str, object]) -> TreeSample:
    costs_value = payload.get("worker_cost_mb")
    costs: tuple[float, ...] = ()
    if isinstance(costs_value, list):
        costs = tuple(
            float(v) for v in cast(list[object], costs_value) if isinstance(v, (int, float))
        )
    return TreeSample(
        procs=int(_num(payload, "procs", 0)),
        workers=int(_num(payload, "workers", 0)),
        cost_gb=_num(payload, "cost_gb", 0.0),
        rss_gb=_num(payload, "rss_gb", 0.0),
        measured=bool(payload.get("measured", False)),
        worker_cost_mb=costs,
    )


def _num(payload: Mapping[str, object], key: str, default: float) -> float:
    value = payload.get(key)
    return float(value) if isinstance(value, (int, float)) else default


def candidates_payload(workers: Sequence[Candidate]) -> list[dict[str, object]]:
    return [
        {"pid": w.pid, "cost_mb": round(w.cost_mb, 1), "age_s": round(w.age_s, 1)} for w in workers
    ]


def candidates_from_payload(value: object) -> tuple[Candidate, ...]:
    if not isinstance(value, list):
        return ()
    out: list[Candidate] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            continue
        entry = cast(Mapping[str, object], item)
        pid = entry.get("pid")
        if not isinstance(pid, int):
            continue
        out.append(
            Candidate(
                pid=pid,
                cost_mb=_num(entry, "cost_mb", 0.0),
                age_s=_num(entry, "age_s", 0.0),
            )
        )
    return tuple(out)


def session_record(
    *,
    target_pid: int,
    target_cmd: str,
    mode: SupervisionMode,
    policy: GuardPolicy,
    machine: Mapping[str, object],
    capabilities: Mapping[str, object],
) -> JournalRecord:
    return JournalRecord(
        "session",
        _now_iso(),
        {
            "schema": JOURNAL_SCHEMA,
            "mode": str(mode),
            "target_pid": target_pid,
            "target_cmd": target_cmd[:CMD_MAX],
            "policy": asdict(policy),
            "machine": dict(machine),
            "capabilities": dict(capabilities),
        },
    )


def sample_record(
    *,
    t: float,
    host: HostSample,
    tree: TreeSample,
    workers: Sequence[Candidate],
    cadence: Cadence,
    decision: Decision,
    outside_gb: float | None,
) -> JournalRecord:
    return JournalRecord(
        "sample",
        _now_iso(),
        {
            "t": round(t, 3),
            "host": host_payload(host),
            "tree": tree_payload(tree),
            "workers": candidates_payload(workers),
            "outside_gb": None if outside_gb is None else round(outside_gb, 3),
            "cadence": {
                "since_last_s": round(cadence.since_last_s, 2),
                "lag_s": round(cadence.lag_s, 2),
            },
            "state": str(decision.state),
            "reason": None if decision.reason is None else str(decision.reason),
            "compressor_rate_gbs": round(decision.compressor_rate_gbs, 3),
            "danger_held_s": round(decision.danger_held_s, 1),
            "shed_total": decision.shed_total,
            "paused": decision.paused,
            "actions": [str(action.kind) for action in decision.actions],
        },
    )


def event_record(kind: str, detail: Mapping[str, object] | None = None) -> JournalRecord:
    return JournalRecord("event", _now_iso(), {"kind": kind, **(detail or {})})


def snapshot_record(
    *,
    elapsed_s: float,
    host: HostSample,
    tree: TreeSample,
    top: Sequence[tuple[int, float, float, str]],
) -> JournalRecord:
    return JournalRecord(
        "snapshot",
        _now_iso(),
        {
            "elapsed_s": round(elapsed_s, 1),
            "host": host_payload(host),
            "tree": tree_payload(tree),
            "worker_pct_mb": percentiles(tree.worker_cost_mb),
            "largest_mb": round(max(tree.worker_cost_mb), 1) if tree.worker_cost_mb else 0.0,
            "top": [
                {"pid": pid, "cost_mb": round(cost, 1), "age_s": round(age, 1), "cmd": cmd[:100]}
                for pid, cost, age, cmd in top
            ],
        },
    )


def summary_record(payload: Mapping[str, object]) -> JournalRecord:
    return JournalRecord("summary", _now_iso(), dict(payload))


class Journal:
    """The JSONL sink. Owns the handle so no caller writes a bare line."""

    def __init__(self, handle: TextIO | None) -> None:
        self.handle = handle
        self.records = 0

    def write(self, record: JournalRecord) -> None:
        if self.handle is None:
            return
        self.handle.write(record.as_line())
        self.records += 1


def iter_records(lines: Iterable[str]) -> Iterator[JournalRecord]:
    """Decode journal lines. Malformed lines are skipped, not fatal; a journal is evidence."""
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            decoded: object = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(decoded, dict):
            continue
        body = cast(dict[str, object], decoded)
        kind = body.pop("record", None)
        ts = body.pop("ts", "")
        if not isinstance(kind, str):
            continue
        yield JournalRecord(kind, ts if isinstance(ts, str) else "", body)


def read_journal(path: Path) -> list[JournalRecord]:
    with path.open("r", encoding="utf-8") as handle:
        return list(iter_records(handle))


@dataclass
class Tally:
    """Everything the monitor did, and what the host looked like while it did it."""

    started: float
    samples: int = 0
    harvests: int = 0
    killed: int = 0
    freed_mb_est: float = 0.0
    aborted: bool = False
    terminated: int = 0
    force_killed: int = 0
    already_gone: int = 0
    signal_denied: int = 0
    reclaimable: list[float] = field(default_factory=list)
    tree_cost: list[float] = field(default_factory=list)
    proc_counts: list[float] = field(default_factory=list)
    largest: list[float] = field(default_factory=list)
    lags: list[float] = field(default_factory=list)
    max_pressure: int = 1

    def observe(self, host: HostSample, tree: TreeSample, lag: float) -> None:
        self.samples += 1
        self.reclaimable.append(host.reclaimable_gb)
        self.tree_cost.append(tree.cost_gb)
        self.proc_counts.append(float(tree.procs))
        self.largest.append(max(tree.worker_cost_mb) if tree.worker_cost_mb else 0.0)
        self.lags.append(lag)
        self.max_pressure = max(self.max_pressure, host.pressure)

    def note_disposition(self, how: str) -> None:
        match how:
            case "term":
                self.terminated += 1
            case "kill":
                self.force_killed += 1
            case "gone":
                self.already_gone += 1
            case "denied":
                self.signal_denied += 1
            case _:
                pass

    def note_harvest(self, killed: Sequence[Candidate]) -> None:
        self.harvests += 1
        self.killed += len(killed)
        self.freed_mb_est += sum(v.cost_mb for v in killed)

    def summary(
        self, now: float, *, pauses: int, paused_total_s: float, late_heartbeats: int
    ) -> dict[str, object]:
        elapsed = max(0.0, now - self.started)
        return {
            "watched_s": round(elapsed, 1),
            "samples": self.samples,
            "sample_hz": round(self.samples / elapsed, 2) if elapsed > 0 else 0.0,
            "interventions": {
                "pauses": pauses,
                "paused_total_s": round(paused_total_s, 1),
                "paused_fraction": round(paused_total_s / elapsed, 3) if elapsed > 0 else 0.0,
                "harvests": self.harvests,
                "processes_killed": self.killed,
                "freed_gb_est": round(self.freed_mb_est / 1024, 2),
                "aborted": self.aborted,
            },
            "dispositions": {
                "terminated_on_sigterm": self.terminated,
                "force_killed_on_sigkill": self.force_killed,
                "already_gone": self.already_gone,
                "signal_denied": self.signal_denied,
            },
            "host": {
                "reclaimable_gb": {
                    "min": round(min(self.reclaimable), 2) if self.reclaimable else 0.0,
                    "mean": (
                        round(sum(self.reclaimable) / len(self.reclaimable), 2)
                        if self.reclaimable
                        else 0.0
                    ),
                    **percentiles(self.reclaimable, (1, 10, 50)),
                },
                "max_pressure": self.max_pressure,
            },
            "tree": {
                "cost_gb": {
                    "max": round(max(self.tree_cost), 2) if self.tree_cost else 0.0,
                    **percentiles(self.tree_cost),
                },
                "procs": {
                    "max": int(max(self.proc_counts)) if self.proc_counts else 0,
                    **percentiles(self.proc_counts),
                },
                "largest_worker_mb": percentiles(self.largest),
            },
            "watchdog_health": {
                "heartbeats_late": late_heartbeats,
                "lag_s": {
                    "max": round(max(self.lags), 2) if self.lags else 0.0,
                    **percentiles(self.lags),
                },
            },
        }
