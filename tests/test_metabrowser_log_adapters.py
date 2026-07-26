"""Unit coverage for the Metaproc-owned MetaBrowser log adapter bridge.

The bridge in ``metaproc.metabrowser_plugin.log_adapters`` registers detectors
and parser factories with MetaBrowser's adapter registry at plugin import time.
These tests pin the detector predicates, the ``MetaprocLogEvent`` to
``metabrowser.LogEvent`` field mapping, and the parser bridge round-trip so a
regression fails here by name instead of silently degrading runpool/process
logs to the generic JSONL fallback view.
"""

from __future__ import annotations

import json

from metabrowser import LogEvent as BrowserLogEvent
from metabrowser import LogParser, detect_adapter

from metaproc.logutil.parsing import LogEvent as MetaprocLogEvent
from metaproc.metabrowser_plugin import register_log_adapters
from metaproc.metabrowser_plugin.log_adapters import (
    _is_process_event,
    _is_runpool_event,
    _ProcessParserBridge,
    _RunPoolParserBridge,
    _to_browser_event,
)

RUNPOOL_POOL_START = {
    "event": "pool_start",
    "pool_id": "pool-7",
    "backend": "local",
    "max_concurrency": 4,
    "ts": "2026-07-14T00:00:00Z",
}

PROCESS_STEP_START = {
    "event": "step_start",
    "step_id": "mine/extract",
    "process": "earnings",
    "ts": "2026-07-14T00:00:01Z",
}


def test_runpool_detector_accepts_only_pool_start() -> None:
    assert _is_runpool_event(RUNPOOL_POOL_START)
    assert not _is_runpool_event(PROCESS_STEP_START)
    assert not _is_runpool_event({"event": "pool_shutdown"})
    assert not _is_runpool_event({"type": "thread.started"})
    assert not _is_runpool_event({})


def test_process_detector_requires_known_event_and_domain_marker() -> None:
    assert _is_process_event(PROCESS_STEP_START)
    assert _is_process_event({"event": "process_start", "process": "earnings"})
    assert _is_process_event({"event": "item_complete", "item": "AAPL"})
    # Known event name without a process/step_id/item_* marker is rejected.
    assert not _is_process_event({"event": "step_start"})
    # Unknown event names are rejected even with a marker present.
    assert not _is_process_event({"event": "pool_start", "process": "earnings"})
    assert not _is_process_event({})


def test_to_browser_event_maps_every_field() -> None:
    source = MetaprocLogEvent(
        kind="result",
        summary="[process_complete] earnings",
        adapter="process",
        provider="anthropic",
        timestamp="2026-07-14T00:00:02Z",
        is_error=True,
        is_done=True,
        cost_usd=1.25,
        duration_s=3.5,
        raw={"event": "process_complete"},
        summary_parts=[{"type": "text", "text": "done"}],
    )
    bridged = _to_browser_event(source)
    assert isinstance(bridged, BrowserLogEvent)
    for field_name in (
        "kind",
        "summary",
        "adapter",
        "provider",
        "timestamp",
        "is_error",
        "is_done",
        "cost_usd",
        "duration_s",
        "raw",
        "summary_parts",
    ):
        assert getattr(bridged, field_name) == getattr(source, field_name)


def test_parser_bridges_satisfy_protocol_and_translate_events() -> None:
    runpool = _RunPoolParserBridge()
    process = _ProcessParserBridge()
    assert isinstance(runpool, LogParser)
    assert isinstance(process, LogParser)
    assert runpool.adapter_name == "runpool"
    assert process.adapter_name == "process"

    events = runpool.parse_line(json.dumps(RUNPOOL_POOL_START))
    assert [type(event) for event in events] == [BrowserLogEvent]
    assert events[0].kind == "init"
    assert events[0].adapter == "runpool"
    assert "pool-7" in events[0].summary
    assert runpool.flush() == []

    events = process.parse_line(json.dumps(PROCESS_STEP_START))
    assert [type(event) for event in events] == [BrowserLogEvent]
    assert events[0].adapter == "process"
    assert "mine/extract" in events[0].summary
    assert process.flush() == []


def test_registration_is_idempotent_and_detects_domain_logs() -> None:
    # Importing metaproc.metabrowser_plugin already registered the adapters;
    # a second call must pass the identical registration tuple (no-op).
    register_log_adapters()
    register_log_adapters()

    assert detect_adapter([json.dumps(RUNPOOL_POOL_START)]) == "runpool"
    assert detect_adapter([json.dumps(PROCESS_STEP_START)]) == "process"
    # Non-domain input falls through to MetaBrowser's builtin/unknown handling.
    assert detect_adapter([json.dumps({"event": "unrelated"})]) == "unknown"
