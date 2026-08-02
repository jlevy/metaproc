"""Provider-meter coverage from authoritative agent terminal records."""

from __future__ import annotations

import json
from pathlib import Path

from metaproc.logutil.agent_provider_meters import AgentProviderMeterSource
from metaproc.logutil.parsing import LogFile
from metaproc.logutil.resource_event_extract import extract_resource_events
from metaproc.models.resources import CoverageState, HierarchyRef, UsageEvent


def _extract(path: Path) -> list[UsageEvent]:
    log_file = LogFile(path, 0)
    log_events = log_file.read_new_events()
    return [
        event
        for event in extract_resource_events(
            log_path=path,
            log_file=log_file,
            log_events=log_events,
            hierarchy=HierarchyRef(run_id="run_provider-meter-test", step_node_id="research"),
            source_kind="agent_log",
            source_path="research/session.jsonl",
            provider_meter_sources=[AgentProviderMeterSource()],
        )
        if isinstance(event, UsageEvent)
    ]


def _meter(events: list[UsageEvent], product: str, meter: str):
    return next(
        quantity
        for event in events
        if event.provider is not None and event.provider.product == product
        for quantity in event.meters
        if quantity.key.meter == meter
    )


def test_claude_reports_exact_turn_and_server_web_request_counts(tmp_path: Path) -> None:
    path = tmp_path / "claude.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"},
                {
                    "type": "result",
                    "is_error": False,
                    "num_turns": 3,
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "server_tool_use": {
                            "web_search_requests": 2,
                            "web_fetch_requests": 0,
                        },
                    },
                },
            )
        )
        + "\n"
    )

    events = _extract(path)

    turns = _meter(events, "llm", "turns")
    assert turns.coverage is CoverageState.MEASURED
    assert turns.actual_quantity == 3
    assert _meter(events, "llm", "agent_invocations").actual_quantity == 1
    llm_requests = _meter(events, "llm", "requests")
    assert llm_requests.coverage is CoverageState.UNMEASURED
    search_requests = _meter(events, "web_search", "requests")
    assert search_requests.coverage is CoverageState.MEASURED
    assert search_requests.actual_quantity == 2
    fetch_requests = _meter(events, "web_fetch", "requests")
    assert fetch_requests.coverage is CoverageState.MEASURED
    assert fetch_requests.actual_quantity == 0
    search_event = next(
        event for event in events if event.provider and event.provider.product == "web_search"
    )
    assert search_event.metrics.api_requests == 2


def test_codex_completed_turn_is_measured_but_api_requests_are_not_inferred(
    tmp_path: Path,
) -> None:
    path = tmp_path / "codex.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {"type": "thread.started", "thread_id": "thread_fixture"},
                {"type": "turn.started"},
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                },
            )
        )
        + "\n"
    )

    events = _extract(path)

    assert _meter(events, "llm", "turns").actual_quantity == 1
    assert _meter(events, "llm", "agent_invocations").actual_quantity == 1
    assert _meter(events, "llm", "requests").coverage is CoverageState.UNMEASURED
    assert all(event.metrics.api_requests is None for event in events)


def test_gemini_turn_coverage_stays_unmeasured_when_stats_have_no_call_count(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gemini.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {"type": "init", "model": "gemini-3.5-flash", "session_id": "ses_fixture"},
                {
                    "type": "tool_use",
                    "tool_name": "google_web_search",
                    "tool_id": "search_fixture",
                    "parameters": {"query": "bounded query"},
                },
                {
                    "type": "tool_result",
                    "tool_id": "search_fixture",
                    "status": "success",
                    "output": "redacted result",
                },
                {
                    "type": "result",
                    "status": "success",
                    "stats": {"input_tokens": 10, "output_tokens": 2, "tool_calls": 1},
                },
            )
        )
        + "\n"
    )

    events = _extract(path)

    assert _meter(events, "llm", "turns").coverage is CoverageState.UNMEASURED
    assert _meter(events, "llm", "agent_invocations").actual_quantity == 1
    assert _meter(events, "llm", "requests").coverage is CoverageState.UNMEASURED
    assert _meter(events, "web_search", "requests").coverage is CoverageState.UNMEASURED
