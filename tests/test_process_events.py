"""Tests for ProcessEventLogger — orchestrator DAG event logging."""

from __future__ import annotations

import json
from pathlib import Path

from metaproc.runpool.process_events import ProcessEventLogger


def test_process_lifecycle_events(tmp_path: Path) -> None:
    """Full lifecycle: process_start → step events → process_complete."""
    log_path = tmp_path / ".logs" / "process-events.jsonl"

    with ProcessEventLogger(log_path) as logger:
        logger.process_start("mine", "run-1", "local", 3)
        logger.level_start(0, ["generate-record"])
        logger.step_start("generate-record", "agent")
        logger.step_complete("generate-record", 120.5)
        logger.level_complete(0, 120.5)
        logger.level_start(1, ["qa-check", "create-mine-summary"])
        logger.step_start("qa-check", "code")
        logger.step_complete("qa-check", 5.3)
        logger.step_skip("create-mine-summary", "previously completed")
        logger.level_complete(1, 5.3)
        logger.process_complete("mine", "run-1", completed=2, failed=0, skipped=1, elapsed_s=125.8)

    lines = log_path.read_text().strip().split("\n")
    events = [json.loads(line) for line in lines]

    assert len(events) == 11
    assert events[0]["event"] == "process_start"
    assert events[0]["process"] == "mine"
    assert events[0]["step_count"] == 3
    assert "ts" in events[0]

    assert events[1]["event"] == "level_start"
    assert events[1]["steps"] == ["generate-record"]

    assert events[2]["event"] == "step_start"
    assert events[2]["step_id"] == "generate-record"
    assert events[2]["mode"] == "agent"

    assert events[3]["event"] == "step_complete"
    assert events[3]["elapsed_s"] == 120.5

    assert events[8]["event"] == "step_skip"
    assert events[8]["reason"] == "previously completed"

    assert events[10]["event"] == "process_complete"
    assert events[10]["completed"] == 2
    assert events[10]["elapsed_s"] == 125.8


def test_step_fail_and_blocked(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"

    with ProcessEventLogger(log_path) as logger:
        logger.step_start("generate-record", "agent")
        logger.step_fail("generate-record", 30.0, error="exit code 1")
        logger.step_blocked("qa-check")

    events = [json.loads(line) for line in log_path.read_text().strip().split("\n")]
    assert events[1]["event"] == "step_fail"
    assert events[1]["error"] == "exit code 1"
    assert events[2]["event"] == "step_blocked"
    assert events[2]["step_id"] == "qa-check"


def test_worker_dispatch_event(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"

    with ProcessEventLogger(log_path) as logger:
        logger.worker_dispatch("generate-record", num_workers=4, items_count=500)

    event = json.loads(log_path.read_text().strip())
    assert event["event"] == "worker_dispatch"
    assert event["num_workers"] == 4
    assert event["items_count"] == 500
