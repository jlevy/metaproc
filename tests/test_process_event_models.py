"""Tests for the typed `ProcessEvent` discriminated union (B4, spec §6.6 + §6.2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from metaproc.runpool.process_event_models import (
    ItemCompleteEvent,
    ItemFailEvent,
    ItemStartEvent,
    ProcessEvent,
    ProcessStartEvent,
    StepCompleteEvent,
    StepStartEvent,
    WorkerDispatchEvent,
)
from metaproc.runpool.process_events import ProcessEventLogger, read_process_events


def _ts() -> datetime:
    return datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)


# ── Discriminated union ────────────────────────────────────────────


def test_event_discriminator_default_set_on_each_subtype() -> None:
    assert (
        ProcessStartEvent(
            ts=_ts(), process="mine", run_id="r1", backend="local", step_count=3
        ).event
        == "process_start"
    )
    assert StepStartEvent(ts=_ts(), step_id="s1", mode="agent").event == "step_start"
    assert StepCompleteEvent(ts=_ts(), step_id="s1", elapsed_s=1.0).event == "step_complete"
    assert (
        WorkerDispatchEvent(ts=_ts(), step_id="s1", num_workers=4, items_count=10).event
        == "worker_dispatch"
    )
    assert ItemStartEvent(ts=_ts(), step_id="s1", item_key="AAPL").event == "item_start"
    assert ItemCompleteEvent(ts=_ts(), step_id="s1", item_key="AAPL").event == "item_complete"
    assert ItemFailEvent(ts=_ts(), step_id="s1", item_key="AAPL").event == "item_fail"


def test_typed_union_round_trip() -> None:
    adapter: TypeAdapter[ProcessEvent] = TypeAdapter(ProcessEvent)
    raw = {
        "ts": "2026-04-24T12:00:00Z",
        "event": "item_fail",
        "step_id": "postprocess",
        "item_key": "AAPL/2026-04-22",
        "error": "rate-limited",
        "failure_class": "rate_limit",
    }
    parsed = adapter.validate_python(raw)
    assert isinstance(parsed, ItemFailEvent)
    assert parsed.failure_class == "rate_limit"
    serialised = json.loads(adapter.dump_json(parsed))
    assert serialised["event"] == "item_fail"


def test_unknown_event_discriminator_rejected() -> None:
    adapter: TypeAdapter[ProcessEvent] = TypeAdapter(ProcessEvent)
    with pytest.raises(ValidationError):
        adapter.validate_python({"ts": "2026-04-24T12:00:00Z", "event": "made_up", "step_id": "s"})


def test_extra_fields_rejected() -> None:
    """`extra=forbid` so silent schema drift never happens."""
    with pytest.raises(ValidationError):
        ProcessStartEvent.model_validate(
            {
                "ts": _ts(),
                "process": "mine",
                "run_id": "r1",
                "backend": "local",
                "step_count": 3,
                "something_unexpected": "boom",
            }
        )


# ── Logger emits the new item_* events ─────────────────────────────


def test_item_start_writes_typed_line(tmp_path: Path) -> None:
    target = tmp_path / "process-events.jsonl"
    with ProcessEventLogger(target) as logger:
        logger.item_start("postprocess", "AAPL", worker_id="worker-3")

    payload = json.loads(target.read_text().strip())
    assert payload["event"] == "item_start"
    assert payload["step_id"] == "postprocess"
    assert payload["item_key"] == "AAPL"
    assert payload["worker_id"] == "worker-3"
    assert "ts" in payload


def test_item_complete_records_elapsed(tmp_path: Path) -> None:
    target = tmp_path / "process-events.jsonl"
    with ProcessEventLogger(target) as logger:
        logger.item_complete("postprocess", "AAPL", elapsed_s=42.123)

    payload = json.loads(target.read_text().strip())
    assert payload["event"] == "item_complete"
    assert payload["elapsed_s"] == 42.1  # rounded to 1 decimal


def test_item_fail_carries_error_and_class(tmp_path: Path) -> None:
    target = tmp_path / "process-events.jsonl"
    with ProcessEventLogger(target) as logger:
        logger.item_fail(
            "postprocess",
            "AAPL",
            error="provider 429",
            failure_class="rate_limit",
            elapsed_s=3.5,
            worker_id="w1",
        )

    payload = json.loads(target.read_text().strip())
    assert payload["event"] == "item_fail"
    assert payload["error"] == "provider 429"
    assert payload["failure_class"] == "rate_limit"
    assert payload["worker_id"] == "w1"
    assert payload["elapsed_s"] == 3.5


def test_subgraph_key_stamped_on_every_event(tmp_path: Path) -> None:
    """Composite-process loggers should pin their subgraph context once at construction."""
    target = tmp_path / "process-events.jsonl"
    with ProcessEventLogger(
        target, subgraph_key="extract-items", process_node_id="process:extract-items"
    ) as logger:
        logger.process_start("normalize-items", "run-1", "local", 1)
        logger.step_start("normalize-items", "code", step_node_id="extract-items::normalize-items")
        logger.item_start("normalize-items", "item-1")

    lines = [json.loads(line) for line in target.read_text().splitlines()]
    for payload in lines:
        assert payload["subgraph_key"] == "extract-items"
        assert payload["process_node_id"] == "process:extract-items"
    assert lines[1]["step_node_id"] == "extract-items::normalize-items"


# ── Typed read helper ──────────────────────────────────────────────


def test_read_process_events_round_trips_writer(tmp_path: Path) -> None:
    target = tmp_path / "process-events.jsonl"
    with ProcessEventLogger(target) as logger:
        logger.process_start("mine", "run-1", "local", 2)
        logger.step_start("postprocess", "code")
        logger.item_start("postprocess", "AAPL")
        logger.item_complete("postprocess", "AAPL", elapsed_s=2.0)
        logger.step_complete("postprocess", elapsed_s=2.0)

    parsed = read_process_events(target)
    assert len(parsed) == 5
    assert isinstance(parsed[0], ProcessStartEvent)
    assert isinstance(parsed[2], ItemStartEvent)
    assert isinstance(parsed[3], ItemCompleteEvent)


def test_read_process_events_skips_blank_lines(tmp_path: Path) -> None:
    target = tmp_path / "process-events.jsonl"
    with ProcessEventLogger(target) as logger:
        logger.process_start("mine", "run-1", "local", 1)
    with open(target, "a") as f:
        f.write("\n\n   \n")
    assert len(read_process_events(target)) == 1
