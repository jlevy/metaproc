"""P1.2 — Codex CLI (v0.130+) trace extractor.

Codex switched from a claude-shaped stream-json (handled by claude_agent
with adapter discriminator) to a new schema: `turn.completed` /
`item.completed` events. CodexAgentExtractor reads the new schema; the
old codex-on-claude-schema case stays with ClaudeAgentExtractor.

These tests use real (truncated) fixture data from the 2026-05-26 Tue
synthetic five-adapter batch, exercising the actual wire format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.trace.extractors.codex_agent import (
    CodexAgentExtractor,
    _is_codex_log,
    _looks_like_codex,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trace_agents"
CODEX_JSONL = FIXTURE_DIR / "codex-sample.jsonl"
CODEX_SIDECAR = FIXTURE_DIR / "codex-sample.jsonl.invocation.json"
CLAUDE_JSONL = FIXTURE_DIR / "claude-sample.jsonl"


def _load_fixture_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


@pytest.fixture
def codex_run_dir(tmp_path: Path) -> Path:
    """Build a synthetic run-dir from the codex fixture."""
    run_dir = tmp_path / "run-codex"
    tasks = run_dir / ".logs" / "tasks" / "edge-candidate-ledger" / "MOD"
    tasks.mkdir(parents=True)
    log = tasks / "edge-candidate-ledger_MOD_2026-05-26T06-37-58.jsonl"
    log.write_bytes(CODEX_JSONL.read_bytes())
    sidecar = tasks / (log.name + ".invocation.json")
    sidecar.write_bytes(CODEX_SIDECAR.read_bytes())
    return run_dir


@pytest.fixture
def claude_run_dir(tmp_path: Path) -> Path:
    """Build a synthetic run-dir with a claude log (codex extractor must NOT claim)."""
    run_dir = tmp_path / "run-claude"
    tasks = run_dir / ".logs" / "tasks" / "research-step" / "AAPL"
    tasks.mkdir(parents=True)
    log = tasks / "alt-data-research_AAPL.jsonl"
    log.write_bytes(CLAUDE_JSONL.read_bytes())
    return run_dir


# ── detect ──


def test_detect_returns_true_on_codex_log(codex_run_dir: Path) -> None:
    extractor = CodexAgentExtractor()
    assert extractor.detect(codex_run_dir) is True


def test_detect_returns_false_on_claude_log(claude_run_dir: Path) -> None:
    extractor = CodexAgentExtractor()
    assert extractor.detect(claude_run_dir) is False


def test_detect_returns_false_on_empty_run_dir(tmp_path: Path) -> None:
    extractor = CodexAgentExtractor()
    assert extractor.detect(tmp_path) is False


def test_looks_like_codex_recognizes_turn_completed(tmp_path: Path) -> None:
    """Schema sniff key: turn.completed / item.completed event types."""
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"type": "turn.started"})
        + "\n"
        + json.dumps({"type": "item.completed", "item": {"type": "command_execution"}})
        + "\n"
    )
    assert _looks_like_codex(log) is True


def test_looks_like_codex_returns_false_for_claude_shaped(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"type": "system", "subtype": "init"})
        + "\n"
        + json.dumps({"type": "assistant", "message": {"content": []}})
        + "\n"
    )
    assert _looks_like_codex(log) is False


# ── extract: attempt span ──


def test_extract_emits_attempt_span(codex_run_dir: Path) -> None:
    extractor = CodexAgentExtractor()
    spans = list(extractor.extract(codex_run_dir, trace_id="trace-1"))
    attempts = [s for s in spans if s.kind == "attempt"]
    assert len(attempts) == 1
    a = attempts[0]
    assert a.source == "codex-agent"
    assert a.attributes["step.id"] == "edge-candidate-ledger"
    assert a.attributes["item.key"] == "MOD"


def test_attempt_carries_token_usage(codex_run_dir: Path) -> None:
    """turn.completed.usage.{input,output,cached_input}_tokens land on attempt span."""
    extractor = CodexAgentExtractor()
    spans = list(extractor.extract(codex_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes.get("attempt.tokens_input") == 47228
    assert attempt.attributes.get("attempt.tokens_output") == 4397
    assert attempt.attributes.get("attempt.tokens_cached") == 72320


def test_attempt_carries_thread_id(codex_run_dir: Path) -> None:
    """thread.started.thread_id becomes attempt.session_id."""
    extractor = CodexAgentExtractor()
    spans = list(extractor.extract(codex_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    session_id = attempt.attributes.get("attempt.session_id")
    assert isinstance(session_id, str)
    assert len(session_id) > 0


# ── extract: tool_call spans ──


def test_extract_emits_bash_spans_for_command_execution(codex_run_dir: Path) -> None:
    """item.completed where item.type=command_execution → bash tool_call span."""
    extractor = CodexAgentExtractor()
    spans = list(extractor.extract(codex_run_dir, trace_id="trace-1"))
    bash_spans = [
        s for s in spans if s.kind == "tool_call" and s.attributes.get("tool.name") == "bash"
    ]
    assert len(bash_spans) >= 1
    for s in bash_spans:
        assert s.attributes.get("tool.family") == "shell"
        assert s.attributes.get("tool.operation") == "execute"


def test_file_change_emits_file_edit_span_with_path(codex_run_dir: Path) -> None:
    """item.completed with item.type=file_change → file_edit span carrying path."""
    extractor = CodexAgentExtractor()
    spans = list(extractor.extract(codex_run_dir, trace_id="trace-1"))
    file_edits = [
        s for s in spans if s.kind == "tool_call" and s.attributes.get("tool.name") == "file_edit"
    ]
    assert len(file_edits) >= 1
    for s in file_edits:
        assert s.attributes.get("tool.family") == "file"
        assert s.attributes.get("tool.operation") == "write"
        assert isinstance(s.attributes.get("tool.input.path"), str)
        assert s.attributes.get("tool.edit_count", 0) >= 1


def test_consecutive_same_path_edits_collapse(tmp_path: Path) -> None:
    """C1a: three consecutive same-path file_change events → one span with edit_count=3."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    same_path_edit = lambda i: {
        "type": "item.completed",
        "item": {
            "id": f"item_{i}",
            "type": "file_change",
            "changes": [{"path": "/repo/foo.md", "kind": "edit"}],
        },
    }
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "t"}),
                json.dumps(same_path_edit(0)),
                json.dumps(same_path_edit(1)),
                json.dumps(same_path_edit(2)),
                json.dumps({"type": "turn.completed", "usage": {}}),
            ]
        )
        + "\n"
    )
    extractor = CodexAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    file_edits = [s for s in spans if s.attributes.get("tool.name") == "file_edit"]
    assert len(file_edits) == 1
    assert file_edits[0].attributes["tool.edit_count"] == 3
    assert file_edits[0].attributes["tool.input.path"] == "/repo/foo.md"


def test_interleaved_path_edits_do_not_collapse(tmp_path: Path) -> None:
    """C1a: edit A, edit B, edit A → three separate spans."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"

    def edit(path: str, idx: int) -> dict[str, Any]:
        return {
            "type": "item.completed",
            "item": {
                "id": f"item_{idx}",
                "type": "file_change",
                "changes": [{"path": path, "kind": "edit"}],
            },
        }

    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "t"}),
                json.dumps(edit("/a", 0)),
                json.dumps(edit("/b", 1)),
                json.dumps(edit("/a", 2)),
                json.dumps({"type": "turn.completed", "usage": {}}),
            ]
        )
        + "\n"
    )
    extractor = CodexAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    file_edits = [s for s in spans if s.attributes.get("tool.name") == "file_edit"]
    assert len(file_edits) == 3
    assert [s.attributes["tool.input.path"] for s in file_edits] == ["/a", "/b", "/a"]
    assert all(s.attributes["tool.edit_count"] == 1 for s in file_edits)


def test_agent_message_is_not_a_tool_call(tmp_path: Path) -> None:
    """item.completed with item.type=agent_message → no tool_call span."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "t"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_0",
                            "type": "agent_message",
                            "text": "I'll start the analysis now.",
                        },
                    }
                ),
                json.dumps({"type": "turn.completed", "usage": {}}),
            ]
        )
        + "\n"
    )
    extractor = CodexAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    assert tool_calls == []


# ── adapter metadata propagation ──


def test_attempt_carries_sidecar_adapter_metadata(tmp_path: Path) -> None:
    """P0.2 metadata keys propagate to attempt span attributes.

    The fixture sidecar is pre-P0.2 (no adapter_type / adapter_trace_source),
    so we synthesize a P0.2-shaped sidecar to test the new code path.
    """
    tasks = tmp_path / ".logs" / "tasks" / "edge-candidate-ledger" / "MOD"
    tasks.mkdir(parents=True)
    log = tasks / "log.jsonl"
    log.write_bytes(CODEX_JSONL.read_bytes())
    sidecar = log.with_suffix(".jsonl.invocation.json")
    sidecar.write_text(
        json.dumps(
            {
                "argv": ["codex", "--print"],
                "metadata": {
                    "adapter_type": "codex-cli",
                    "adapter_provider": "openai",
                    "adapter_trace_source": "codex-agent",
                    "adapter_model": "gpt-5.5",
                    "lane_id": "lane-codex",
                    "execution_profile": "synthetic-five-adapter",
                },
            }
        )
    )
    extractor = CodexAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes.get("adapter.type") == "codex-cli"
    assert attempt.attributes.get("adapter.provider") == "openai"
    assert attempt.attributes.get("adapter.trace_source") == "codex-agent"
    assert attempt.attributes.get("adapter.model") == "gpt-5.5"
    assert attempt.attributes.get("lane_id") == "lane-codex"
    assert attempt.attributes.get("execution_profile") == "synthetic-five-adapter"


# ── disambiguation: codex vs claude logs ──


def test_is_codex_log_true_when_schema_has_turn_completed(tmp_path: Path) -> None:
    """Schema is authoritative — sidecar is only a tie-breaker hint."""
    log = tmp_path / "x.jsonl"
    log.write_text(json.dumps({"type": "turn.completed", "usage": {}}) + "\n")
    assert _is_codex_log(log) is True


def test_is_codex_log_false_for_claude_shape_even_with_codex_sidecar(
    tmp_path: Path,
) -> None:
    """Old codex (claude-shape schema + codex-cli sidecar) belongs to claude_agent,
    not codex_agent. The discriminator is the schema, not the sidecar.
    """
    log = tmp_path / "x.jsonl"
    log.write_text(json.dumps({"type": "assistant", "message": {}}) + "\n")
    sidecar = log.with_suffix(".jsonl.invocation.json")
    sidecar.write_text(json.dumps({"metadata": {"adapter_type": "codex-cli"}}))
    assert _is_codex_log(log) is False


def test_is_codex_log_false_for_claude_shape(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(json.dumps({"type": "assistant", "message": {}}) + "\n")
    assert _is_codex_log(log) is False
