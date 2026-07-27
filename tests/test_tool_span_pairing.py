"""Tests for the tool-span pairing helper (B8, spec §6.3)."""

from __future__ import annotations

from metaproc.logutil.parsing import LogEvent
from metaproc.logutil.tool_spans import ToolSpan, pair_tool_events


def _call(
    adapter: str,
    tool: str,
    ts: str,
    *,
    execution_id: str | None = None,
    raw: dict[str, object] | None = None,
) -> LogEvent:
    raw = raw or {}
    raw.setdefault("toolName", tool)
    if execution_id is not None:
        raw["executionId"] = execution_id
    return LogEvent(
        kind="tool_call",
        summary=f"[call:{tool}]",
        adapter=adapter,
        timestamp=ts,
        raw=raw,
    )


def _result(
    adapter: str,
    tool: str,
    ts: str,
    *,
    is_error: bool = False,
    execution_id: str | None = None,
    raw: dict[str, object] | None = None,
) -> LogEvent:
    raw = raw or {}
    raw.setdefault("toolName", tool)
    if execution_id is not None:
        raw["executionId"] = execution_id
    return LogEvent(
        kind="tool_result",
        summary=f"[result:{tool}]",
        adapter=adapter,
        timestamp=ts,
        is_error=is_error,
        raw=raw,
    )


def test_simple_call_then_result_pairs_with_duration() -> None:
    events = [
        _call("pi", "Bash", "2026-04-24T12:00:00Z"),
        _result("pi", "Bash", "2026-04-24T12:00:02.5Z"),
    ]
    spans = pair_tool_events(events)
    assert len(spans) == 1
    span = spans[0]
    assert span.tool_name == "Bash"
    assert span.adapter == "pi"
    assert span.duration_s == 2.5
    assert span.failure_class is None
    assert not span.is_error
    assert span.tool_path == ("execution", "tool", "bash", "Bash")


def test_error_result_classified_as_tool_error() -> None:
    events = [
        _call("pi", "Bash", "2026-04-24T12:00:00Z"),
        _result("pi", "Bash", "2026-04-24T12:00:01Z", is_error=True),
    ]
    span = pair_tool_events(events)[0]
    assert span.is_error
    assert span.failure_class == "tool_error"


def test_execution_id_takes_precedence_over_fifo_within_name() -> None:
    """Two interleaved Bash calls should pair by executionId, not arrival order."""
    events = [
        _call("pi", "Bash", "2026-04-24T12:00:00Z", execution_id="A"),
        _call("pi", "Bash", "2026-04-24T12:00:01Z", execution_id="B"),
        # B finishes before A.
        _result("pi", "Bash", "2026-04-24T12:00:03Z", execution_id="B"),
        _result("pi", "Bash", "2026-04-24T12:00:05Z", execution_id="A"),
    ]
    spans = pair_tool_events(events)
    assert len(spans) == 2
    # First span is the result for B (started @ 12:00:01, ended @ 12:00:03).
    assert spans[0].duration_s == 2.0
    # Second span is the result for A (started @ 12:00:00, ended @ 12:00:05).
    assert spans[1].duration_s == 5.0


def test_fifo_pairing_within_name_when_no_execution_id() -> None:
    events = [
        _call("claude", "Read", "2026-04-24T12:00:00Z"),
        _call("claude", "Read", "2026-04-24T12:00:01Z"),
        _result("claude", "Read", "2026-04-24T12:00:02Z"),
        _result("claude", "Read", "2026-04-24T12:00:04Z"),
    ]
    spans = pair_tool_events(events)
    assert [s.duration_s for s in spans] == [2.0, 3.0]


def test_unmatched_call_yields_orphan_span() -> None:
    events = [_call("pi", "Bash", "2026-04-24T12:00:00Z")]
    span = pair_tool_events(events)[0]
    assert span.failure_class == "unmatched_start"
    assert span.is_error
    assert span.duration_s is None


def test_unmatched_result_yields_orphan_span() -> None:
    events = [_result("pi", "Bash", "2026-04-24T12:00:00Z")]
    span = pair_tool_events(events)[0]
    assert span.failure_class == "unmatched_result"
    assert span.duration_s is None


def test_tool_path_classifies_built_in_families() -> None:
    builtins = {
        "Bash": "bash",
        "Read": "read",
        "Write": "write",
        "Edit": "edit",
        "Grep": "grep",
    }
    for tool, family in builtins.items():
        span = pair_tool_events(
            [
                _call("claude", tool, "2026-04-24T12:00:00Z"),
                _result("claude", tool, "2026-04-24T12:00:01Z"),
            ]
        )[0]
        assert span.tool_path == ("execution", "tool", family, tool)


def test_tool_path_classifies_mcp_tools() -> None:
    span = pair_tool_events(
        [
            _call("claude", "mcp__github__list_issues", "2026-04-24T12:00:00Z"),
            _result("claude", "mcp__github__list_issues", "2026-04-24T12:00:01Z"),
        ]
    )[0]
    assert span.tool_path == ("execution", "tool", "mcp", "mcp__github__list_issues")


def test_tool_path_falls_back_to_adapter_for_unknown_tool() -> None:
    span = pair_tool_events(
        [
            _call("codex", "some_codex_thing", "2026-04-24T12:00:00Z"),
            _result("codex", "some_codex_thing", "2026-04-24T12:00:01Z"),
        ]
    )[0]
    assert span.tool_path == ("execution", "tool", "codex", "some_codex_thing")


def test_summary_used_when_raw_lacks_tool_name() -> None:
    """Summary fallback handles parsers that omit the toolName field."""
    call = LogEvent(
        kind="tool_call",
        summary="[call:WebSearch] something",
        adapter="claude",
        timestamp="2026-04-24T12:00:00Z",
    )
    result = LogEvent(
        kind="tool_result",
        summary="[result:WebSearch] ok",
        adapter="claude",
        timestamp="2026-04-24T12:00:01Z",
    )
    span = pair_tool_events([call, result])[0]
    assert span.tool_name == "WebSearch"
    assert span.tool_path[2] == "web"


def test_negative_duration_yields_none_not_negative_value() -> None:
    """Out-of-order timestamps shouldn't poison downstream rollups with negative seconds."""
    events = [
        _call("pi", "Bash", "2026-04-24T12:00:05Z"),
        _result("pi", "Bash", "2026-04-24T12:00:00Z"),
    ]
    span = pair_tool_events(events)[0]
    assert span.duration_s is None


def test_isinstance_check_returns_tool_spans() -> None:
    spans = pair_tool_events(
        [_call("pi", "Bash", "2026-04-24T12:00:00Z"), _result("pi", "Bash", "2026-04-24T12:00:01Z")]
    )
    assert all(isinstance(s, ToolSpan) for s in spans)
