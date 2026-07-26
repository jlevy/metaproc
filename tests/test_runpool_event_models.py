"""Tests for typed pydantic event models, including auth events.

internal-reference (Phase 4 of plan-2026-05-03). Adds typed model coverage
for AuthOutcomeEvent, AuthLeaseAcquiredEvent, and RetryScheduledEvent
so read_runpool_events no longer silently drops them.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from metaproc.runpool.event_models import (
    AuthLeaseAcquiredEvent,
    AuthOutcomeEvent,
    ResourceTelemetryErrorEvent,
    RetryScheduledEvent,
    RunPoolEvent,
)
from metaproc.runpool.event_reader import read_runpool_events

_adapter = TypeAdapter(RunPoolEvent)


class TestAuthOutcomeEvent:
    def test_schema_v2_full_round_trip(self):
        raw = {
            "event": "auth_outcome",
            "ts": "2026-05-04T17:14:00+00:00",
            "schema_version": 2,
            "adapter": "claude-code-cli",
            "label": "alt2",
            "classification": "ok",
            "run_id": "run-x",
            "step_id": "predict-ticker",
            "item": "AAPL",
            "attempt": 1,
            "session_log_path": "/tmp/session.jsonl",
        }
        ev = _adapter.validate_python(raw)
        assert isinstance(ev, AuthOutcomeEvent)
        assert ev.label == "alt2"
        assert ev.attempt == 1
        assert ev.session_log_path == "/tmp/session.jsonl"

    def test_schema_v1_legacy_event_parses_with_defaults(self):
        # Pre-2026-05-03 event, no join keys, no schema_version field.
        raw = {
            "event": "auth_outcome",
            "ts": "2026-04-27T17:00:00+00:00",
            "adapter": "claude-code-cli",
            "label": "alt1",
            "classification": "cooling",
        }
        ev = _adapter.validate_python(raw)
        assert isinstance(ev, AuthOutcomeEvent)
        assert ev.schema_version == 1
        assert ev.run_id == ""
        assert ev.attempt == 0


class TestAuthLeaseAcquiredEvent:
    def test_full_payload(self):
        raw = {
            "event": "auth_lease_acquired",
            "ts": "2026-05-04T16:59:00+00:00",
            "schema_version": 2,
            "adapter": "claude-code-cli",
            "label": "alt2",
            "slot_dir": "/tmp/slot",
            "run_id": "run-x",
            "step_id": "predict-ticker",
            "item": "AAPL",
            "attempt": 0,
            "session_log_path": "/tmp/session.jsonl",
            "policy": "round-robin",
            "active_lease_count": {"alt1": 12, "alt2": 13},
        }
        ev = _adapter.validate_python(raw)
        assert isinstance(ev, AuthLeaseAcquiredEvent)
        assert ev.label == "alt2"
        assert ev.policy == "round-robin"
        assert ev.active_lease_count == {"alt1": 12, "alt2": 13}

    def test_minimal_payload_optional_fields_default(self):
        raw = {
            "event": "auth_lease_acquired",
            "ts": "2026-05-04T17:00:00+00:00",
            "adapter": "claude-code-cli",
            "label": "alt1",
        }
        ev = _adapter.validate_python(raw)
        assert isinstance(ev, AuthLeaseAcquiredEvent)
        assert ev.policy == ""
        assert ev.active_lease_count == {}


class TestRetryScheduledEvent:
    def test_round_trip(self):
        raw = {
            "event": "retry_scheduled",
            "ts": "2026-05-04T17:01:00+00:00",
            "label": "alt1",
            "attempt": 2,
            "backoff_s": 30.0,
        }
        ev = _adapter.validate_python(raw)
        assert isinstance(ev, RetryScheduledEvent)
        assert ev.attempt == 2


class TestResourceTelemetryErrorEvent:
    def test_round_trip(self):
        raw = {
            "event": "resource_telemetry_error",
            "ts": "2026-05-17T16:03:00+00:00",
            "error": "unsupported runpool resource telemetry",
            "action": "reduced_to_min_concurrency",
            "current_concurrency": 1,
            "effective_target": 1,
        }
        ev = _adapter.validate_python(raw)
        assert isinstance(ev, ResourceTelemetryErrorEvent)
        assert ev.action == "reduced_to_min_concurrency"


class TestReadRunpoolEventsPicksUpAuth:
    """Before internal-reference, read_runpool_events silently dropped
    auth_outcome / auth_lease_acquired because they weren't in the
    discriminated union. Now they show up in the typed reader.
    """

    def test_typed_reader_returns_auth_events(self, tmp_path: Path):
        events_path = tmp_path / "runpool-events.jsonl"
        events_path.write_text(
            "\n".join(
                json.dumps(ev)
                for ev in [
                    {
                        "event": "pool_start",
                        "ts": "2026-05-04T16:00:00+00:00",
                        "pool_id": "p1",
                        "backend": "local",
                        "max_concurrency": 10,
                    },
                    {
                        "event": "auth_lease_acquired",
                        "ts": "2026-05-04T16:01:00+00:00",
                        "adapter": "claude-code-cli",
                        "label": "alt1",
                        "policy": "round-robin",
                    },
                    {
                        "event": "auth_outcome",
                        "ts": "2026-05-04T16:02:00+00:00",
                        "adapter": "claude-code-cli",
                        "label": "alt1",
                        "classification": "ok",
                    },
                    {
                        "event": "retry_scheduled",
                        "ts": "2026-05-04T16:03:00+00:00",
                        "label": "alt1",
                        "attempt": 1,
                        "backoff_s": 5.0,
                    },
                ]
            )
        )
        events = read_runpool_events(events_path)
        kinds = [type(e).__name__ for e in events]
        assert "AuthLeaseAcquiredEvent" in kinds
        assert "AuthOutcomeEvent" in kinds
        assert "RetryScheduledEvent" in kinds
        assert "PoolStartEvent" in kinds
