"""Journal round trips and replay determinism."""

from __future__ import annotations

import io
from pathlib import Path

from safeproc.journal import (
    JOURNAL_SCHEMA,
    Journal,
    candidates_from_payload,
    event_record,
    host_from_payload,
    host_payload,
    iter_records,
    percentiles,
    sample_record,
    session_record,
    tree_from_payload,
    tree_payload,
)
from safeproc.models import (
    ALARM_CRITICAL,
    ActionKind,
    Candidate,
    GuardPolicy,
    OutsideReading,
    SupervisionMode,
    TreeSample,
)
from safeproc.policy import PressureEngine
from safeproc.replay import policy_from_session, replay_journal, replay_records
from tests.conftest import host


def test_percentiles_nearest_rank() -> None:
    assert percentiles([]) == {"p50": 0.0, "p90": 0.0, "p99": 0.0}
    assert percentiles([1, 2, 3, 4, 5]) == {"p50": 3.0, "p90": 5.0, "p99": 5.0}


def test_host_and_tree_round_trip() -> None:
    sample = host(7.5, ALARM_CRITICAL, stall_full_pct=12.5)
    back = host_from_payload(host_payload(sample))
    assert back.reclaimable_gb == round(sample.reclaimable_gb, 3)
    assert back.pressure == ALARM_CRITICAL
    assert back.stall_full_pct == 12.5
    assert back.suspension_gb == round(sample.suspension_gb, 2)
    tree = TreeSample(
        procs=4, workers=2, cost_gb=1.5, rss_gb=1.2, measured=True, worker_cost_mb=(900.0, 600.0)
    )
    assert tree_from_payload(tree_payload(tree)) == tree


def test_records_round_trip_through_lines() -> None:
    buffer = io.StringIO()
    journal = Journal(buffer)
    journal.write(
        session_record(
            target_pid=42,
            target_cmd="x" * 500,
            mode=SupervisionMode.MONITORED,
            policy=GuardPolicy(danger_gb=4.0, worker_patterns=("agent",)),
            machine={"host": "h"},
            capabilities={"psi": "absent"},
        )
    )
    journal.write(event_record("pause", {"why": "test"}))
    buffer.write("this is not json\n")
    records = list(iter_records(buffer.getvalue().splitlines()))
    assert [r.kind for r in records] == ["session", "event"]
    session = records[0].payload
    assert session["schema"] == JOURNAL_SCHEMA
    assert len(str(session["target_cmd"])) == 200
    policy = policy_from_session(records)
    assert policy is not None
    assert policy.danger_gb == 4.0
    assert policy.worker_patterns == ("agent",)


def _record_live_run(path: Path) -> int:
    """Run the engine live, journal every sample, and return how many shed actions ran."""
    policy = GuardPolicy(intervene=True, confirm_s=1.0, interval_s=0.5, shed_settle_s=1.0)
    engine = PressureEngine(policy)
    shed = 0
    with path.open("w", encoding="utf-8") as handle:
        journal = Journal(handle)
        journal.write(
            session_record(
                target_pid=1,
                target_cmd="root",
                mode=SupervisionMode.MONITORED,
                policy=policy,
                machine={},
                capabilities={},
            )
        )
        candidates = [Candidate(pid=100 + i, cost_mb=1000.0 - i, age_s=20.0) for i in range(8)]
        samples = [host(12.0, 1)] * 4 + [host(2.0, ALARM_CRITICAL)] * 12 + [host(12.0, 1)] * 8
        t = 50.0
        for index, sample in enumerate(samples):
            t += policy.interval_s
            outside_gb = 40.0 if 6 <= index <= 8 else 0.0
            outside: OutsideReading | None = None

            def read_outside(value: float = outside_gb) -> OutsideReading:
                nonlocal outside
                outside = OutsideReading(value)
                return outside

            tree = TreeSample(
                procs=10,
                workers=len(candidates),
                cost_gb=sum(c.cost_mb for c in candidates) / 1024,
                rss_gb=7.0,
                measured=True,
                worker_cost_mb=tuple(c.cost_mb for c in candidates),
            )
            decision = engine.evaluate(t, sample, tree, candidates, read_outside)
            shed += sum(1 for a in decision.actions if a.kind is ActionKind.SHED)
            journal.write(
                sample_record(
                    t=t,
                    host=sample,
                    tree=tree,
                    workers=candidates,
                    cadence=engine.last_cadence,
                    decision=decision,
                    outside_gb=None if outside is None else outside.total_gb,
                )
            )
    return shed


def test_replay_reproduces_the_recorded_decisions(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    shed = _record_live_run(path)
    assert shed >= 1
    result = replay_journal(path)
    assert result.skipped == 0
    assert len(result.steps) == 24
    assert result.mismatches == []
    assert result.count(ActionKind.SHED) == shed
    assert result.count(ActionKind.HOLD_NOT_AT_FAULT) == 1
    summary = result.as_dict()
    assert summary["mismatches"] == 0
    assert summary["max_state"] in {"critical", "watch"}


def test_replay_with_a_different_policy_detects_drift(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    _record_live_run(path)
    stricter = GuardPolicy(intervene=True, confirm_s=10.0, interval_s=0.5)
    result = replay_journal(path, stricter)
    assert result.mismatches, "a stricter confirmation window must change the decisions"


def test_replay_skips_malformed_samples() -> None:
    records = list(
        iter_records(
            [
                '{"record":"sample","ts":"x","t":"not a number","host":{},"tree":{}}',
                '{"record":"sample","ts":"x","t":1.0,"host":{"reclaimable_gb":12,"free_gb":4,"pressure":1},"tree":{"procs":1,"workers":0,"cost_gb":0,"rss_gb":0,"measured":false}}',
            ]
        )
    )
    result = replay_records(records)
    assert result.skipped == 1
    assert len(result.steps) == 1


def test_candidates_from_payload_ignores_garbage() -> None:
    assert candidates_from_payload(None) == ()
    assert candidates_from_payload([{"pid": "x"}, {"pid": 3, "cost_mb": 1.5}]) == (
        Candidate(pid=3, cost_mb=1.5, age_s=0.0),
    )
