"""Tests for fan-out item event emission helpers (F1, spec §6.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaproc.commands.run_process import (
    _current_worker_id,
    _emit_fan_out_item_results,
    _emit_fan_out_item_starts,
)
from metaproc.runpool.process_events import ProcessEventLogger


def test_emit_fan_out_item_starts_writes_one_event_per_item(tmp_path: Path) -> None:
    log_path = tmp_path / "process-events.jsonl"
    contexts = [{"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "GOOG"}]
    with ProcessEventLogger(log_path) as events:
        _emit_fan_out_item_starts(
            events, step_id="postprocess", each="ticker", item_contexts=contexts
        )

    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(lines) == 3
    assert all(line["event"] == "item_start" for line in lines)
    assert [line["item_key"] for line in lines] == ["AAPL", "MSFT", "GOOG"]
    assert all(line["step_id"] == "postprocess" for line in lines)


def test_emit_fan_out_item_results_classifies_success_and_failure(tmp_path: Path) -> None:
    log_path = tmp_path / "process-events.jsonl"
    results = [("AAPL", 0), ("MSFT", 1), ("GOOG", 0)]
    with ProcessEventLogger(log_path) as events:
        _emit_fan_out_item_results(events, step_id="postprocess", results=results)

    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert lines[0]["event"] == "item_complete"
    assert lines[0]["item_key"] == "AAPL"
    assert lines[1]["event"] == "item_fail"
    assert lines[1]["item_key"] == "MSFT"
    assert lines[1]["error"] == "exit code 1"
    assert lines[2]["event"] == "item_complete"


def test_worker_id_propagated_to_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAPROC_WORKER_ID", "worker-7")
    assert _current_worker_id() == "worker-7"

    log_path = tmp_path / "process-events.jsonl"
    with ProcessEventLogger(log_path) as events:
        _emit_fan_out_item_starts(
            events,
            step_id="postprocess",
            each="ticker",
            item_contexts=[{"ticker": "AAPL"}],
        )
        _emit_fan_out_item_results(events, step_id="postprocess", results=[("AAPL", 0)])

    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert lines[0]["worker_id"] == "worker-7"
    assert lines[1]["worker_id"] == "worker-7"


def test_current_worker_id_returns_none_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("METAPROC_WORKER_ID", raising=False)
    assert _current_worker_id() is None
