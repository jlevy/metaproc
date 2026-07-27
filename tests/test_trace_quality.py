"""Unit tests for the --quality validators (C6)."""

from __future__ import annotations

from typing import Any

from metaproc.trace.quality import (
    directive_compliance_rows,
    format_directive_compliance,
    format_runbook_completion,
    runbook_completion_rows,
)
from metaproc.trace.schema import TraceEvent

DIRECTIVES = {"research": ("setup.md", "sources.json", "context.json")}


def _span(**overrides: Any) -> TraceEvent:
    base: dict[str, Any] = {
        "trace_id": "t",
        "span_id": "x",
        "name": "n",
        "kind": "tool_call",
        "source": "claude-agent",
        "ts_start": "2026-05-12T00:00:00Z",
        # Default ts_end so sessions look terminal; the active-run-aware
        # validators treat ts_end-less sessions as
        # in_progress, so tests that want to exercise terminal-state
        # classification need this default. Explicitly drop ts_end to
        # exercise the in_progress path (see tests below).
        "ts_end": "2026-05-12T00:00:01Z",
    }
    base.update(overrides)
    return TraceEvent(**base)  # type: ignore[arg-type]


# --- runbook-completion ---


def test_runbook_completion_marks_session_with_write_ok():
    session = _span(
        span_id="sess1",
        kind="agent_session",
        attributes={"step.id": "research", "item.key": "item-1"},
    )
    write_call = _span(
        span_id="w1",
        parent_span_id="sess1",
        attributes={"tool.name": "Write"},
    )
    rows = runbook_completion_rows([session, write_call])
    assert len(rows) == 1
    assert rows[0]["writes"] == 1
    assert rows[0]["status"] == "ok"


def test_runbook_completion_marks_zero_writes_as_missing_output():
    session = _span(
        span_id="sess1",
        kind="agent_session",
        attributes={"step.id": "research", "item.key": "item-1"},
    )
    read_only = _span(
        span_id="r1",
        parent_span_id="sess1",
        attributes={"tool.name": "Read"},
    )
    rows = runbook_completion_rows([session, read_only])
    assert rows[0]["status"] == "missing_output"
    assert rows[0]["writes"] == 0


def test_runbook_completion_counts_edit_as_write():
    session = _span(
        span_id="sess1",
        kind="agent_session",
        attributes={"step.id": "research", "item.key": "item-1"},
    )
    edit_call = _span(
        span_id="e1",
        parent_span_id="sess1",
        attributes={"tool.name": "Edit"},
    )
    rows = runbook_completion_rows([session, edit_call])
    assert rows[0]["status"] == "ok"


def test_runbook_completion_empty_returns_empty():
    assert runbook_completion_rows([]) == []
    assert "no agent_session" in format_runbook_completion([])


# --- directive-compliance ---


def test_directive_compliance_all_inputs_read():
    session = _span(
        span_id="sess1",
        kind="agent_session",
        attributes={"step.id": "research", "item.key": "item-1"},
    )
    reads = [
        _span(
            span_id=f"r{i}",
            parent_span_id="sess1",
            attributes={"tool.name": "Read", "tool.input.file_path": p},
        )
        for i, p in enumerate(
            [
                "/run/item-1/setup.md",
                "/run/item-1/sources.json",
                "/run/item-1/context.json",
            ]
        )
    ]
    rows = directive_compliance_rows([session, *reads], DIRECTIVES)
    assert len(rows) == 1
    assert rows[0]["read"] == 3
    assert rows[0]["expected"] == 3
    assert rows[0]["status"] == "ok"
    assert rows[0]["missing"] == []


def test_directive_compliance_missing_input_marked_non_compliant():
    session = _span(
        span_id="sess1",
        kind="agent_session",
        attributes={"step.id": "research", "item.key": "item-1"},
    )
    read_partial = _span(
        span_id="r1",
        parent_span_id="sess1",
        attributes={"tool.name": "Read", "tool.input.file_path": "/run/item-1/setup.md"},
    )
    rows = directive_compliance_rows([session, read_partial], DIRECTIVES)
    assert rows[0]["status"] == "non_compliant"
    assert "sources.json" in rows[0]["missing"]
    assert "context.json" in rows[0]["missing"]


def test_directive_compliance_skips_steps_without_directives():
    """A scaffold-roster step has no directive list; the validator should
    not emit a row for it.
    """
    session = _span(
        span_id="sess1",
        kind="agent_session",
        attributes={"step.id": "unconfigured", "item.key": "item-1"},
    )
    rows = directive_compliance_rows([session], DIRECTIVES)
    assert rows == []


def test_format_directive_compliance_includes_summary_line():
    rows = [
        {
            "step.id": "research",
            "item.key": "item-1",
            "expected": 3,
            "read": 2,
            "missing": ["context.json"],
            "status": "non_compliant",
        }
    ]
    text = format_directive_compliance(rows)
    assert "non_compliant" in text
    assert "context.json" in text
    assert "1 non_compliant" in text


# --- active-run awareness ---


def test_runbook_completion_marks_in_flight_session_as_in_progress():
    """A session whose ts_end is null is still running; do not call it missing."""
    session = _span(
        span_id="sess1",
        kind="agent_session",
        ts_end=None,
        attributes={"step.id": "research", "item.key": "item-1"},
    )
    rows = runbook_completion_rows([session])
    assert len(rows) == 1
    assert rows[0]["status"] == "in_progress"


def test_directive_compliance_marks_in_flight_session_as_in_progress():
    session = _span(
        span_id="sess1",
        kind="agent_session",
        ts_end=None,
        attributes={"step.id": "research", "item.key": "item-1"},
    )
    rows = directive_compliance_rows([session], DIRECTIVES)
    assert len(rows) == 1
    assert rows[0]["status"] == "in_progress"


def test_format_runbook_completion_surfaces_in_progress_count():
    rows = [
        {
            "step.id": "research",
            "item.key": "item-1",
            "writes": 0,
            "status": "in_progress",
        }
    ]
    text = format_runbook_completion(rows)
    assert "1 in-progress" in text


def test_format_directive_compliance_surfaces_in_progress_count():
    rows = [
        {
            "step.id": "research",
            "item.key": "item-1",
            "expected": 3,
            "read": 0,
            "missing": ["setup.md"],
            "status": "in_progress",
        }
    ]
    text = format_directive_compliance(rows)
    assert "1 in-progress" in text
