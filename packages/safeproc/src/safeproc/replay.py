"""Deterministic replay: a recorded journal back through the same policy.

Replay is offline, needs no broker or platform, and is the test that keeps policy
changes honest: predictive signals may create false embargoes on healthy runs, but a
measured destructive action on a run that completed is a defect. It also detects drift
between a journal's recorded decisions and what the current engine would decide.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import cast

from safeproc.journal import (
    JournalRecord,
    candidates_from_payload,
    host_from_payload,
    read_journal,
    tree_from_payload,
)
from safeproc.models import ActionKind, Decision, GuardPolicy, OutsideReading, PressureState

_STATE_RANK: dict[PressureState, int] = {
    PressureState.HEALTHY: 0,
    PressureState.WATCH: 1,
    PressureState.EMBARGO: 2,
    PressureState.CRITICAL: 3,
    PressureState.CATASTROPHIC: 4,
}
from safeproc.policy import PressureEngine


@dataclass(frozen=True)
class ReplayStep:
    t: float
    decision: Decision
    recorded_actions: tuple[str, ...]

    @property
    def replayed_actions(self) -> tuple[str, ...]:
        return tuple(str(action.kind) for action in self.decision.actions)

    @property
    def matches(self) -> bool:
        return self.replayed_actions == self.recorded_actions


@dataclass
class ReplayResult:
    policy: GuardPolicy
    steps: list[ReplayStep] = field(default_factory=list)
    skipped: int = 0

    @property
    def mismatches(self) -> list[ReplayStep]:
        return [step for step in self.steps if not step.matches]

    def count(self, kind: ActionKind) -> int:
        return sum(
            1 for step in self.steps for action in step.decision.actions if action.kind is kind
        )

    @property
    def destructive(self) -> int:
        return self.count(ActionKind.SHED) + self.count(ActionKind.ABORT)

    def as_dict(self) -> dict[str, object]:
        return {
            "samples": len(self.steps),
            "skipped": self.skipped,
            "mismatches": len(self.mismatches),
            "pauses": self.count(ActionKind.PAUSE),
            "resumes": self.count(ActionKind.RESUME),
            "sheds": self.count(ActionKind.SHED),
            "aborts": self.count(ActionKind.ABORT),
            "predictive_holds": self.count(ActionKind.PREDICTIVE_HOLD),
            "holds_not_at_fault": self.count(ActionKind.HOLD_NOT_AT_FAULT),
            "late_heartbeats": self.count(ActionKind.HEARTBEAT_LATE),
            "max_state": str(
                max(
                    (step.decision.state for step in self.steps),
                    key=lambda state: _STATE_RANK[state],
                    default=PressureState.HEALTHY,
                )
            ),
        }


def policy_from_session(records: Iterable[JournalRecord]) -> GuardPolicy | None:
    """The policy a journal was recorded with, if its session record carries one."""
    for record in records:
        if record.kind != "session":
            continue
        raw = record.payload.get("policy")
        if not isinstance(raw, dict):
            return None
        source = cast(Mapping[str, object], raw)
        known = [f.name for f in fields(GuardPolicy)]
        kwargs: dict[str, object] = {}
        for name in known:
            if name not in source:
                continue
            value: object = source[name]
            if name == "worker_patterns" and isinstance(value, list):
                patterns = cast(list[object], value)
                kwargs[name] = tuple(str(v) for v in patterns)
            else:
                kwargs[name] = value
        try:
            return GuardPolicy(**kwargs)  # type: ignore[arg-type]
        except TypeError:
            return None
    return None


def replay_records(
    records: Sequence[JournalRecord], policy: GuardPolicy | None = None
) -> ReplayResult:
    """Feed every sample through a fresh engine, in order, using the recorded clock."""
    effective = policy or policy_from_session(records) or GuardPolicy()
    engine = PressureEngine(effective)
    result = ReplayResult(policy=effective)
    for record in records:
        if record.kind != "sample":
            continue
        payload = record.payload
        t = payload.get("t")
        host_raw = payload.get("host")
        tree_raw = payload.get("tree")
        if (
            not isinstance(t, (int, float))
            or not isinstance(host_raw, dict)
            or not isinstance(tree_raw, dict)
        ):
            result.skipped += 1
            continue
        host = host_from_payload(cast(Mapping[str, object], host_raw))
        tree = tree_from_payload(cast(Mapping[str, object], tree_raw))
        workers = candidates_from_payload(payload.get("workers"))
        outside_value = payload.get("outside_gb")
        outside_gb = float(outside_value) if isinstance(outside_value, (int, float)) else 0.0

        def read_outside(value: float = outside_gb) -> OutsideReading:
            return OutsideReading(value)

        decision = engine.evaluate(float(t), host, tree, workers, read_outside)
        recorded_raw = payload.get("actions")
        recorded = (
            tuple(str(a) for a in cast(list[object], recorded_raw))
            if isinstance(recorded_raw, list)
            else ()
        )
        result.steps.append(ReplayStep(float(t), decision, recorded))
    return result


def replay_journal(path: Path, policy: GuardPolicy | None = None) -> ReplayResult:
    return replay_records(read_journal(path), policy)
