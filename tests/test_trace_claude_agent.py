"""Unit tests for the Claude agent JSONL extractor."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.trace import TraceEvent
from metaproc.trace.extractors.claude_agent import ClaudeAgentExtractor


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "run-abc"


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _gzip_and_remove(path: Path) -> Path:
    gz_path = path.with_name(path.name + ".gz")
    with path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        dst.write(src.read())
    path.unlink()
    return gz_path


def _write_minimal_attempt(
    run_dir: Path,
    *,
    step: str,
    item_key: str,
    session_id: str,
    tool_use_id: str,
    tool_name: str = "Read",
    tool_input: dict[str, object] | None = None,
    is_error: bool = False,
) -> Path:
    path = (
        run_dir
        / ".logs"
        / "tasks"
        / step
        / item_key
        / f"{step}_{item_key}_2026-05-12T00-00-00.jsonl"
    )
    events: list[dict[str, object]] = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": tool_name,
                        "input": tool_input or {"file_path": "/x.md"},
                    }
                ]
            },
            "session_id": session_id,
            "timestamp": "2026-05-12T00:00:01Z",
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "tool_use_id": tool_use_id,
                        "type": "tool_result",
                        "content": "ok" if not is_error else "boom",
                        "is_error": is_error,
                    }
                ]
            },
            "session_id": session_id,
            "timestamp": "2026-05-12T00:00:02Z",
        },
        {
            "type": "result",
            "total_cost_usd": 0.42,
            "num_turns": 1,
            "duration_ms": 1000,
            "session_id": session_id,
            "is_error": False,
        },
    ]
    _write_jsonl(path, events)
    return path


def test_detect_returns_false_on_empty_run_dir(run_dir: Path):
    run_dir.mkdir()
    assert ClaudeAgentExtractor().detect(run_dir) is False


def test_detect_returns_true_when_jsonl_present(run_dir: Path):
    _write_minimal_attempt(
        run_dir,
        step="research-step",
        item_key="MNDY",
        session_id="s1",
        tool_use_id="t1",
    )
    assert ClaudeAgentExtractor().detect(run_dir) is True


def test_extract_reads_compressed_jsonl_present(run_dir: Path):
    path = _write_minimal_attempt(
        run_dir,
        step="research-step",
        item_key="MNDY",
        session_id="s1",
        tool_use_id="t1",
    )
    _gzip_and_remove(path)

    assert ClaudeAgentExtractor().detect(run_dir) is True
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    assert [span.kind for span in spans].count("attempt") == 1
    assert [span.kind for span in spans].count("tool_call") == 1


def test_extract_yields_attempt_session_and_tool_spans(run_dir: Path):
    _write_minimal_attempt(
        run_dir,
        step="research-step",
        item_key="MNDY",
        session_id="s1",
        tool_use_id="t1",
        tool_name="Read",
        tool_input={"file_path": "/some/bundle.json"},
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    by_kind: dict[str, list[TraceEvent]] = {}
    for s in spans:
        by_kind.setdefault(s.kind, []).append(s)
    assert len(by_kind["attempt"]) == 1
    assert len(by_kind["agent_session"]) == 1
    assert len(by_kind["tool_call"]) == 1
    attempt = by_kind["attempt"][0]
    tool = by_kind["tool_call"][0]
    session = by_kind["agent_session"][0]
    assert attempt.attributes["step.id"] == "research-step"
    assert attempt.attributes["item.key"] == "MNDY"
    assert attempt.status == "ok"
    assert session.parent_span_id == attempt.span_id
    assert tool.parent_span_id == session.span_id
    assert tool.attributes["tool.name"] == "Read"
    assert tool.attributes["tool.input.file_path"] == "/some/bundle.json"
    assert tool.attributes["tool.use_id"] == "t1"


def test_extract_classifies_tool_error(run_dir: Path):
    _write_minimal_attempt(
        run_dir,
        step="research-step",
        item_key="MNDY",
        session_id="s1",
        tool_use_id="t1",
        tool_name="Bash",
        tool_input={"command": "ep-arena trends MNDY"},
        is_error=True,
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    tool_spans = [s for s in spans if s.kind == "tool_call"]
    assert len(tool_spans) == 1
    assert tool_spans[0].status == "error"
    assert tool_spans[0].error is not None
    assert tool_spans[0].error["kind"] == "tool_error"


def test_extract_preserves_native_web_tool_inputs(run_dir: Path):
    _write_minimal_attempt(
        run_dir,
        step="deep-research",
        item_key="MNDY",
        session_id="s1",
        tool_use_id="search1",
        tool_name="WebSearch",
        tool_input={"query": "MNDY earnings customer growth"},
    )
    _write_minimal_attempt(
        run_dir,
        step="deep-research",
        item_key="HMSY",
        session_id="s2",
        tool_use_id="fetch1",
        tool_name="WebFetch",
        tool_input={
            "url": "https://example.com/hims-q4",
            "prompt": "Summarize only revenue, guidance, and management commentary.",
        },
    )

    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    tools = {s.attributes["tool.name"]: s for s in spans if s.kind == "tool_call"}
    search = tools["WebSearch"]
    fetch = tools["WebFetch"]
    assert search.attributes["tool.family"] == "web"
    assert search.attributes["tool.operation"] == "search"
    assert search.attributes["tool.usage_role"] == "research"
    assert search.attributes["tool.input.query"] == "MNDY earnings customer growth"
    assert search.attributes["source_origin"] == "agent_web_search"
    assert fetch.attributes["tool.operation"] == "fetch"
    assert fetch.attributes["tool.input.url"] == "https://example.com/hims-q4"
    assert "revenue" in fetch.attributes["tool.input.prompt_summary"]
    assert fetch.attributes["source_origin"] == "agent_web_fetch"


def test_extract_emits_internal_span_for_compaction(run_dir: Path):
    path = run_dir / ".logs" / "tasks" / "deep-market-research" / "CEVA" / "x.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "compaction",
                "version": 1,
                "original_size": 230000,
                "original_lines": 70,
                "timestamp": "2026-05-12T00:00:00Z",
            },
            {
                "type": "result",
                "total_cost_usd": 0.1,
                "num_turns": 1,
                "duration_ms": 500,
                "session_id": "s1",
                "is_error": False,
                "timestamp": "2026-05-12T00:00:05Z",
            },
        ],
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    internal = [s for s in spans if s.kind == "internal"]
    assert len(internal) == 1
    assert internal[0].attributes["internal.kind"] == "compaction"
    assert internal[0].attributes["internal.original_size"] == 230000


def test_extract_marks_attempt_error_when_no_result_block(run_dir: Path):
    """Crashed attempt: no 'type=result' line means the session didn't end cleanly."""
    path = run_dir / ".logs" / "tasks" / "research-step" / "MNDY" / "x.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]
                },
                "session_id": "s1",
                "timestamp": "2026-05-12T00:00:01Z",
            }
        ],
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.status == "error"
    # Unmatched tool_use → tool_call with status=error
    tool = next(s for s in spans if s.kind == "tool_call")
    assert tool.status == "error"
    assert tool.error is not None
    assert tool.error["kind"] == "unmatched_tool_use"


def test_extract_reads_invocation_json_sidecar(run_dir: Path):
    path = _write_minimal_attempt(
        run_dir,
        step="research-step",
        item_key="MNDY",
        session_id="s1",
        tool_use_id="t1",
    )
    invocation = path.with_suffix(".jsonl.invocation.json")
    invocation.write_text(
        json.dumps(
            {
                "model": "claude-opus-4-7",
                "effort": "high",
                "tools": ["Read", "Write", "Bash"],
                "permission_mode": "bypassPermissions",
            }
        )
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes["adapter.model"] == "claude-opus-4-7"
    assert attempt.attributes["adapter.tools"] == ["Read", "Write", "Bash"]


def test_invocation_sidecar_adapter_sets_agent_source(run_dir: Path):
    path = _write_minimal_attempt(
        run_dir,
        step="research-step",
        item_key="MNDY",
        session_id="s1",
        tool_use_id="t1",
    )
    invocation = path.with_suffix(".jsonl.invocation.json")
    invocation.write_text(
        json.dumps(
            {
                "argv": ["codex", "exec", "prompt"],
                "metadata": {"adapter": "codex-cli"},
                "env_redacted": {"EXECUTION_PROFILE": "codex-gpt55"},
            }
        )
    )

    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    assert {s.source for s in spans} == {"codex-agent"}
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes["adapter.type"] == "codex-cli"
    assert attempt.attributes["execution_profile"] == "codex-gpt55"


def _write_failed_attempt(
    run_dir: Path,
    *,
    step: str,
    item_key: str,
    result_text: str,
) -> Path:
    """Helper: write a JSONL whose only meaningful event is a failed result
    (is_error=True). Mirrors the shape produced by Claude Code when an
    agent invocation crashes after a quota/auth/timeout signal — no
    assistant or tool_use messages, just the terminal result line.
    """
    path = (
        run_dir
        / ".logs"
        / "tasks"
        / step
        / item_key
        / f"{step}_{item_key}_2026-05-13T09-26-30.jsonl"
    )
    events: list[dict[str, object]] = [
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "result": result_text,
            "session_id": "s1",
            "timestamp": "2026-05-13T09:26:31Z",
        }
    ]
    _write_jsonl(path, events)
    return path


def test_attempt_carries_quota_exhausted_error_code_and_message(run_dir: Path):
    _write_failed_attempt(
        run_dir,
        step="price-attributed-timeline",
        item_key="BABA",
        result_text="You're out of extra usage · resets 3:10am (America/Los_Angeles)",
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.status == "error"
    assert attempt.error is not None
    assert attempt.error["code"] == "quota_exhausted"
    assert "out of extra usage" in attempt.error["message"]


def test_attempt_carries_auth_required_error_code(run_dir: Path):
    _write_failed_attempt(
        run_dir,
        step="business-setup",
        item_key="MNDY",
        result_text="Authentication required: token expired",
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.error is not None
    assert attempt.error["code"] == "auth_required"


def test_attempt_carries_agent_timeout_error_code(run_dir: Path):
    _write_failed_attempt(
        run_dir,
        step="market-timeline",
        item_key="MNDY",
        result_text="Operation timed out after 600s",
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.error is not None
    assert attempt.error["code"] == "agent_timeout"


def test_attempt_carries_generic_agent_error_code(run_dir: Path):
    _write_failed_attempt(
        run_dir,
        step="market-timeline",
        item_key="MNDY",
        result_text="Some random failure that doesn't match any known pattern",
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.error is not None
    assert attempt.error["code"] == "agent_error"
    assert "Some random failure" in attempt.error["message"]


def test_attempt_has_no_error_when_is_error_false(run_dir: Path):
    _write_minimal_attempt(
        run_dir,
        step="research-step",
        item_key="MNDY",
        session_id="s1",
        tool_use_id="t1",
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.status == "ok"
    assert attempt.error is None


def test_attempt_crashed_no_result_event_classified_as_agent_error(run_dir: Path):
    """No 'type=result' line at all — crashed attempt. Status=error, code=agent_error,
    no message (since there's no result text to copy)."""
    path = run_dir / ".logs" / "tasks" / "research-step" / "MNDY" / "x.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]
                },
                "session_id": "s1",
                "timestamp": "2026-05-12T00:00:01Z",
            }
        ],
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.status == "error"
    assert attempt.error is not None
    assert attempt.error["code"] == "agent_error"
    assert "message" not in attempt.error


def test_span_ids_are_deterministic_across_runs(run_dir: Path):
    _write_minimal_attempt(
        run_dir,
        step="research-step",
        item_key="MNDY",
        session_id="s1",
        tool_use_id="toolu_xyz",
    )
    first = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    second = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-abc"))
    assert [s.span_id for s in first] == [s.span_id for s in second]
