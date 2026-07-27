"""Tests for shared trace extractor helpers.

Tests the canonical `tool_usage_classification` dispatcher (so every
per-adapter extractor maps its raw tool names through the same family /
operation / usage_role taxonomy) and the `collapse_consecutive_file_edits`
helper (C1a in plan-2026-05-26-multi-adapter-resource-rollup).
"""

from __future__ import annotations

from typing import Any

import pytest

from metaproc.trace.extractors.common import (
    collapse_consecutive_file_edits,
    tool_usage_classification,
)

# ── tool_usage_classification: cross-adapter dispatch ──


@pytest.mark.parametrize(
    ("raw_name", "expected_family", "expected_op"),
    [
        # Claude (PascalCase)
        ("WebSearch", "web", "search"),
        ("WebFetch", "web", "fetch"),
        ("Bash", "shell", "execute"),
        ("Read", "file", "read"),
        ("Edit", "file", "write"),
        ("MultiEdit", "file", "write"),
        ("Grep", "file", "search"),
        # Gemini (snake_case, native names)
        ("google_web_search", "web", "search"),
        ("web_fetch", "web", "fetch"),
        ("run_shell_command", "shell", "execute"),
        ("read_file", "file", "read"),
        ("write_file", "file", "write"),
        ("replace", "file", "write"),
        # Pi (camelCase)
        ("webSearch", "web", "search"),
        ("bash", "shell", "execute"),
        ("read", "file", "read"),
        ("write", "file", "write"),
    ],
)
def test_tool_usage_classification_normalizes_across_adapters(
    raw_name: str, expected_family: str, expected_op: str
) -> None:
    attrs = tool_usage_classification(raw_name, {})
    assert attrs.get("tool.family") == expected_family
    assert attrs.get("tool.operation") == expected_op


def test_tool_usage_classification_preserves_query() -> None:
    """Web-search dispatch threads the query through bounded-text safely."""
    attrs = tool_usage_classification("google_web_search", {"query": "test query"})
    assert attrs.get("tool.input.query") == "test query"
    assert attrs.get("source_origin") == "agent_web_search"


def test_tool_usage_classification_preserves_url() -> None:
    attrs = tool_usage_classification("web_fetch", {"url": "https://example.com"})
    assert attrs.get("tool.input.url") == "https://example.com"
    assert attrs.get("source_origin") == "agent_web_fetch"


def test_tool_usage_classification_preserves_path() -> None:
    attrs = tool_usage_classification("read_file", {"absolute_path": "/etc/hosts"})
    assert attrs.get("tool.input.path") == "/etc/hosts"


def test_tool_usage_classification_unknown_tool_returns_empty() -> None:
    """Unknown tool name → no classification (caller keeps raw name)."""
    assert tool_usage_classification("definitely_not_a_real_tool", {}) == {}


# ── collapse_consecutive_file_edits ──


def _path(ev: dict[str, Any]) -> str | None:
    return ev.get("path") if isinstance(ev.get("path"), str) else None


def _is_edit(ev: dict[str, Any]) -> bool:
    return ev.get("kind") == "file_edit"


def test_collapse_merges_consecutive_same_path() -> None:
    """Three consecutive edits to the same file → one collapsed event."""
    events: list[dict[str, Any]] = [
        {"kind": "file_edit", "path": "/a", "i": 0},
        {"kind": "file_edit", "path": "/a", "i": 1},
        {"kind": "file_edit", "path": "/a", "i": 2},
    ]
    collapsed = collapse_consecutive_file_edits(events, path_of=_path, is_file_edit=_is_edit)
    assert len(collapsed) == 1
    assert collapsed[0]["edit_count"] == 3
    assert collapsed[0]["path"] == "/a"
    assert collapsed[0]["event"]["i"] == 0


def test_collapse_keeps_interleaved_paths_separate() -> None:
    """Edit A, edit B, edit A → three spans (A, B, A)."""
    events: list[dict[str, Any]] = [
        {"kind": "file_edit", "path": "/a"},
        {"kind": "file_edit", "path": "/b"},
        {"kind": "file_edit", "path": "/a"},
    ]
    collapsed = collapse_consecutive_file_edits(events, path_of=_path, is_file_edit=_is_edit)
    assert [c["path"] for c in collapsed] == ["/a", "/b", "/a"]
    assert all(c["edit_count"] == 1 for c in collapsed)


def test_collapse_non_edit_event_breaks_run() -> None:
    """Non-file-edit event interleaved with same-path edits breaks the run."""
    events: list[dict[str, Any]] = [
        {"kind": "file_edit", "path": "/a"},
        {"kind": "bash", "cmd": "ls"},
        {"kind": "file_edit", "path": "/a"},
    ]
    collapsed = collapse_consecutive_file_edits(events, path_of=_path, is_file_edit=_is_edit)
    assert len(collapsed) == 3
    assert collapsed[0].get("edit_count") == 1
    assert "edit_count" not in collapsed[1]
    assert collapsed[2].get("edit_count") == 1


def test_collapse_preserves_non_edit_passthrough() -> None:
    """Non-edit events pass through unchanged in the output sequence."""
    events: list[dict[str, Any]] = [
        {"kind": "bash", "cmd": "ls"},
        {"kind": "file_edit", "path": "/a"},
        {"kind": "file_edit", "path": "/a"},
        {"kind": "bash", "cmd": "cat /a"},
    ]
    collapsed = collapse_consecutive_file_edits(events, path_of=_path, is_file_edit=_is_edit)
    assert len(collapsed) == 3
    assert "edit_count" not in collapsed[0]
    assert collapsed[1]["edit_count"] == 2
    assert "edit_count" not in collapsed[2]


def test_collapse_handles_empty_input() -> None:
    assert collapse_consecutive_file_edits([], path_of=_path, is_file_edit=_is_edit) == []
