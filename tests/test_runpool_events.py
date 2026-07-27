"""Tests for EventLogger."""

from __future__ import annotations

import json
from pathlib import Path

from metaproc.runpool.events import EventLogger


class TestEventLogger:
    def test_open_and_write(self, tmp_path: Path):
        log_path = tmp_path / "events.jsonl"
        with EventLogger(log_path) as logger:
            logger.pool_start("pool-1", "local", 10)

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "pool_start"
        assert event["pool_id"] == "pool-1"
        assert event["backend"] == "local"
        assert event["max_concurrency"] == 10
        assert "ts" in event

    def test_all_event_types(self, tmp_path: Path):
        log_path = tmp_path / "events.jsonl"
        with EventLogger(log_path) as logger:
            logger.pool_start("pool-1", "local", 10)
            logger.process_start(123, None, "local", "test-label")
            logger.process_exit(123, None, "local", "test-label", 0, 42.5, 500000)
            logger.process_kill(456, None, "local", "kill-label", "rss_limit", 4000000000)
            logger.concurrency_adjust(10, 15, "pressure_normal", 3)
            logger.pressure_check("normal", 55.0)
            logger.health_sample({"level": "normal", "available_pct": 55.0})
            logger.pool_shutdown("pool-1", 5, 1, 1)

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 8
        events = [json.loads(line) for line in lines]
        assert [e["event"] for e in events] == [
            "pool_start",
            "process_start",
            "process_exit",
            "process_kill",
            "concurrency_adjust",
            "pressure_check",
            "health_sample",
            "pool_shutdown",
        ]

    def test_backend_field_on_process_events(self, tmp_path: Path):
        log_path = tmp_path / "events.jsonl"
        with EventLogger(log_path) as logger:
            logger.process_start(123, None, "local", "test")
            logger.process_exit(123, None, "local", "test", 0, 10.0, None)
            logger.process_kill(456, "ext-1", "mock", "test", "timeout", None)

        lines = log_path.read_text().strip().split("\n")
        events = [json.loads(line) for line in lines]
        assert events[0]["backend"] == "local"
        assert events[1]["backend"] == "local"
        assert events[2]["backend"] == "mock"

    def test_flush_on_write(self, tmp_path: Path):
        log_path = tmp_path / "events.jsonl"
        logger = EventLogger(log_path)
        logger.open()
        logger.pool_start("pool-1", "local", 5)
        # File should be readable immediately (flushed).
        content = log_path.read_text()
        assert "pool_start" in content
        logger.close()

    def test_creates_parent_dirs(self, tmp_path: Path):
        log_path = tmp_path / "deep" / "nested" / "events.jsonl"
        with EventLogger(log_path) as logger:
            logger.pool_start("pool-1", "local", 1)
        assert log_path.exists()

    def test_external_id_in_process_events(self, tmp_path: Path):
        log_path = tmp_path / "events.jsonl"
        with EventLogger(log_path) as logger:
            logger.process_start(None, "job-42/task-0", "mock", "cloud-task")

        event = json.loads(log_path.read_text().strip())
        assert event["external_id"] == "job-42/task-0"
        assert event["pid"] is None

    def test_auth_outcome_event(self, tmp_path: Path):
        log_path = tmp_path / "events.jsonl"
        outcome_dict: dict[str, object] = {
            "schema_version": 2,
            "adapter": "claude-code-cli",
            "label": "alt1",
            "classification": "ok",
        }
        with EventLogger(log_path) as logger:
            logger.auth_outcome(outcome_dict)

        event = json.loads(log_path.read_text().strip())
        assert event["event"] == "auth_outcome"
        assert event["adapter"] == "claude-code-cli"
        assert event["label"] == "alt1"
        assert event["schema_version"] == 2

    def test_auth_lease_acquired_event(self, tmp_path: Path):
        # Schema-v2 acquisition-time companion to auth_outcome.
        log_path = tmp_path / "events.jsonl"
        payload: dict[str, object] = {
            "schema_version": 2,
            "adapter": "claude-code-cli",
            "label": "alt2",
            "slot_dir": "/tmp/slot",
            "run_id": "run-x",
            "step_id": "predict-ticker",
            "item": "AAPL",
            "attempt": 1,
            "session_log_path": "/tmp/run-x/.logs/predict_AAPL.jsonl",
            "policy": "round-robin",
            "active_lease_count": {"alt1": 12, "alt2": 13},
        }
        with EventLogger(log_path) as logger:
            logger.auth_lease_acquired(payload)

        event = json.loads(log_path.read_text().strip())
        assert event["event"] == "auth_lease_acquired"
        assert event["label"] == "alt2"
        assert event["run_id"] == "run-x"
        assert event["item"] == "AAPL"
        assert event["attempt"] == 1
        assert event["policy"] == "round-robin"
        assert event["active_lease_count"] == {"alt1": 12, "alt2": 13}
        assert "ts" in event
