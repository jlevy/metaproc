"""Tests for typed pydantic event models, including auth events.

the fix (Phase 4 of plan-2026-05-03). Adds typed model coverage
for AuthOutcomeEvent, AuthLeaseAcquiredEvent, and RetryScheduledEvent
so read_runpool_events no longer silently drops them.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from metaproc.runpool.event_models import (
    AuthLeaseAcquiredEvent,
    AuthOutcomeEvent,
    AuthSkippedEvent,
    ConcurrencyAdjustEvent,
    HealthSampleEvent,
    PressureCheckEvent,
    QuotaPauseResumedEvent,
    QuotaPauseStartedEvent,
    QuotaPauseTickEvent,
    ResourceTelemetryErrorEvent,
    RetryScheduledEvent,
    RunPoolEvent,
)
from metaproc.runpool.event_reader import read_runpool_events
from metaproc.runpool.events import EventLogger

_adapter = TypeAdapter(RunPoolEvent)


def test_event_logger_round_trips_complete_control_and_health_events(tmp_path: Path) -> None:
    events_path = tmp_path / "runpool-events.jsonl"
    reset_at = "2026-05-04T18:00:00.123456+00:00"
    with EventLogger(events_path) as logger:
        logger.concurrency_adjust(
            8,
            5,
            "pressure_elevated",
            1,
            2,
            memory_ceiling=5,
            provider_ceiling=7,
            operator_cap=9,
            effective_target=5,
            bottleneck="memory-bound",
        )
        logger.pressure_check(
            "elevated",
            24.5,
            swap_used_gb=2.5,
            total_memory_gb=32.0,
            memory_level="elevated",
            swap_delta_gb_per_min=0.3,
            swap_level="normal",
            disk_free_gb=8.0,
            disk_total_gb=500.0,
            disk_used_pct=98.4,
            disk_level="high",
            disk_pressure_cause="active_logs",
            source="macos-memorystatus",
            current_concurrency=5,
            active_count=4,
            pending_count=3,
            memory_ceiling=5,
            provider_ceiling=7,
            operator_cap=9,
            effective_target=5,
            bottleneck="memory-bound",
            active_rss_bytes=4_000,
            active_peak_rss_bytes=5_000,
            active_log_bytes=6_000,
            consecutive_normal=0,
            consecutive_elevated=2,
        )
        logger.health_sample(
            {
                "level": "elevated",
                "available_pct": 24.5,
                "memory_level": "elevated",
                "swap_used_gb": 2.5,
                "total_memory_gb": 32.0,
                "swap_delta_gb_per_min": 0.3,
                "swap_level": "normal",
                "disk_free_gb": 8.0,
                "disk_total_gb": 500.0,
                "disk_used_pct": 98.4,
                "disk_level": "high",
                "disk_pressure_cause": "active_logs",
                "source": "macos-memorystatus",
                "current_concurrency": 5,
                "active_count": 4,
                "pending_count": 3,
                "memory_ceiling": 5,
                "provider_ceiling": 7,
                "operator_cap": 9,
                "effective_target": 5,
                "bottleneck": "memory-bound",
                "active_rss_bytes": 4_000,
                "active_peak_rss_bytes": 5_000,
                "active_log_bytes": 6_000,
                "consecutive_normal": 0,
                "consecutive_elevated": 2,
            }
        )
        logger.quota_pause_started(reset_at, 30.0)
        logger.quota_pause_tick(120.5)
        logger.quota_pause_resumed()

    events = read_runpool_events(events_path)
    assert len(events) == 6
    raw_events = [json.loads(line) for line in events_path.read_text().splitlines()]
    for raw_event in raw_events:
        raw_event["ts"] = datetime.fromisoformat(raw_event["ts"])
    assert [event.model_dump(exclude_none=True) for event in events] == raw_events

    assert [type(event) for event in events] == [
        ConcurrencyAdjustEvent,
        PressureCheckEvent,
        HealthSampleEvent,
        QuotaPauseStartedEvent,
        QuotaPauseTickEvent,
        QuotaPauseResumedEvent,
    ]


def test_historic_minimal_control_and_health_samples_parse() -> None:
    samples = [
        {
            "event": "concurrency_adjust",
            "ts": "2026-04-10T07:02:30+00:00",
            "old": 20,
            "new": 25,
            "reason": "ramp_up",
        },
        {
            "event": "pressure_check",
            "ts": "2026-04-10T07:00:05+00:00",
            "level": "normal",
            "available_pct": 95.0,
        },
        {
            "event": "health_sample",
            "ts": "2026-04-10T07:00:05+00:00",
            "level": "normal",
            "available_pct": 95.0,
        },
    ]

    parsed = [_adapter.validate_python(sample) for sample in samples]
    assert isinstance(parsed[0], ConcurrencyAdjustEvent)
    assert parsed[0].effective_target is None
    assert isinstance(parsed[1], PressureCheckEvent)
    assert parsed[1].active_count is None
    assert parsed[1].consecutive_elevated is None
    assert isinstance(parsed[2], HealthSampleEvent)
    assert parsed[2].memory_level is None
    assert parsed[2].consecutive_elevated is None


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


class TestAuthSkippedEvent:
    def test_adapter_mismatch_payload(self):
        raw = {
            "event": "auth_skipped",
            "ts": "2026-08-24T17:00:00+00:00",
            "schema_version": 1,
            "pool_enabled": False,
            "step_id": "mine",
            "step_adapter": "pi-cli",
            "configured_adapter": "claude-code-cli",
            "reason": "adapter_mismatch",
        }
        ev = _adapter.validate_python(raw)
        assert isinstance(ev, AuthSkippedEvent)
        assert ev.pool_enabled is False
        assert ev.step_adapter == "pi-cli"


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
    """Before the fix, read_runpool_events silently dropped
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
