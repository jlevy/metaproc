"""Unit tests for `ResourceEventLogger` (B1, spec §4.4.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from metaproc.logutil.resource_events import ResourceEventLogger, read_events
from metaproc.models.resources import (
    HierarchyRef,
    ItemFailEvent,
    ItemStartEvent,
    SourceRef,
    SpanStartEvent,
)


def _ts() -> datetime:
    return datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)


def _src() -> SourceRef:
    return SourceRef(kind="agent_log", path=".logs/x.jsonl")


def _hier(**overrides: str | None) -> HierarchyRef:
    return HierarchyRef(run_id="run-1", **overrides)


def test_writer_creates_parent_directory_on_open(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "resource-events.jsonl"
    with ResourceEventLogger(target):
        pass
    assert target.exists()


def test_writer_writes_one_jsonl_line_per_event(tmp_path: Path) -> None:
    target = tmp_path / "resource-events.jsonl"
    with ResourceEventLogger(target) as logger:
        logger.write(
            SpanStartEvent(
                ts=_ts(),
                span_id="sp1",
                hierarchy=_hier(step_node_id="s1"),
                source=_src(),
            )
        )
        logger.write(
            ItemStartEvent(
                ts=_ts(),
                hierarchy=_hier(step_node_id="s1", item_key="AAPL"),
                source=_src(),
            )
        )

    lines = target.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "span_start"
    assert json.loads(lines[1])["event"] == "item_start"


def test_writer_drops_silently_when_unopened(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "never-opened.jsonl"
    logger = ResourceEventLogger(target)
    with caplog.at_level("WARNING"):
        logger.write(SpanStartEvent(ts=_ts(), span_id="sp", hierarchy=_hier(), source=_src()))
    # Per the EventLogger pattern: warn but do not raise.
    assert not target.exists()
    assert any("called before open" in r.message for r in caplog.records)


def test_read_events_round_trips_writer(tmp_path: Path) -> None:
    target = tmp_path / "resource-events.jsonl"
    events = [
        SpanStartEvent(
            ts=_ts(),
            span_id="sp1",
            hierarchy=_hier(step_node_id="s1"),
            source=_src(),
        ),
        ItemFailEvent(
            ts=_ts(),
            hierarchy=_hier(step_node_id="s1", item_key="AAPL"),
            source=_src(),
            error="boom",
            failure_class="rate_limit",
        ),
    ]
    with ResourceEventLogger(target) as logger:
        for ev in events:
            logger.write(ev)

    parsed = read_events(target)
    assert len(parsed) == 2
    assert isinstance(parsed[0], SpanStartEvent)
    assert isinstance(parsed[1], ItemFailEvent)
    assert parsed[1].failure_class == "rate_limit"


def test_read_events_skips_blank_lines(tmp_path: Path) -> None:
    target = tmp_path / "resource-events.jsonl"
    with ResourceEventLogger(target) as logger:
        logger.write(SpanStartEvent(ts=_ts(), span_id="sp", hierarchy=_hier(), source=_src()))
    # Inject blank lines as might happen with editor-touched files.
    with open(target, "a") as f:
        f.write("\n\n   \n")
    parsed = read_events(target)
    assert len(parsed) == 1


def test_read_events_raises_on_bad_json(tmp_path: Path) -> None:
    """Malformed JSON should raise loudly — silent corruption is worse than failure."""

    target = tmp_path / "bad.jsonl"
    target.write_text("{not json}\n")
    with pytest.raises((ValidationError, ValueError)):
        read_events(target)
