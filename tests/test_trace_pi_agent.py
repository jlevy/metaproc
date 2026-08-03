"""P1.4 — Pi CLI trace extractor.

Pi CLI wraps any underlying model (glm-5, gemini-flash, claude-opus, etc.)
and emits camelCase stream-json. These tests use the real fixture at
``pi-sample.jsonl`` from the 2030-01-01 synthetic five-adapter corpus,
plus synthetic logs for edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.trace.extractors.claude_agent import ClaudeAgentExtractor
from metaproc.trace.extractors.pi_agent import (
    PiAgentExtractor,
    _is_pi_log,
    _looks_like_pi,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trace_agents"
PI_JSONL = FIXTURE_DIR / "pi-sample.jsonl"
PI_SIDECAR = FIXTURE_DIR / "pi-sample.jsonl.invocation.json"
CLAUDE_JSONL = FIXTURE_DIR / "claude-sample.jsonl"
CODEX_JSONL = FIXTURE_DIR / "codex-sample.jsonl"


def _load_fixture_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


@pytest.fixture
def pi_run_dir(tmp_path: Path) -> Path:
    """Build a synthetic run-dir from the pi fixture."""
    run_dir = tmp_path / "run-pi"
    tasks = run_dir / ".logs" / "tasks" / "sample-analysis" / "ITEM-001"
    tasks.mkdir(parents=True)
    log = tasks / "sample-analysis_ITEM-001_2030-01-01T03-38-10.jsonl"
    log.write_bytes(PI_JSONL.read_bytes())
    sidecar = tasks / (log.name + ".invocation.json")
    sidecar.write_bytes(PI_SIDECAR.read_bytes())
    return run_dir


@pytest.fixture
def claude_run_dir(tmp_path: Path) -> Path:
    """Build a synthetic run-dir with a claude log (pi extractor must NOT claim)."""
    run_dir = tmp_path / "run-claude"
    tasks = run_dir / ".logs" / "tasks" / "sample-analysis" / "ITEM-002"
    tasks.mkdir(parents=True)
    log = tasks / "sample-analysis_ITEM-002.jsonl"
    log.write_bytes(CLAUDE_JSONL.read_bytes())
    return run_dir


@pytest.fixture
def codex_run_dir(tmp_path: Path) -> Path:
    """Build a synthetic run-dir with a codex log (pi extractor must NOT claim)."""
    run_dir = tmp_path / "run-codex"
    tasks = run_dir / ".logs" / "tasks" / "edge-candidate-ledger" / "MOD"
    tasks.mkdir(parents=True)
    log = tasks / "edge-candidate-ledger_MOD.jsonl"
    log.write_bytes(CODEX_JSONL.read_bytes())
    return run_dir


# ── _looks_like_pi / _is_pi_log ──


def test_looks_like_pi_recognizes_tool_execution_start(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"type": "session", "version": 3, "id": "abc"})
        + "\n"
        + json.dumps({"type": "tool_execution_start", "toolCallId": "c1", "toolName": "bash"})
        + "\n"
    )
    assert _looks_like_pi(log) is True


def test_looks_like_pi_recognizes_agent_end(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(json.dumps({"type": "agent_end", "messages": []}) + "\n")
    assert _looks_like_pi(log) is True


def test_looks_like_pi_false_for_claude_shaped(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"type": "system", "subtype": "init"})
        + "\n"
        + json.dumps({"type": "assistant", "message": {"content": []}})
        + "\n"
    )
    assert _looks_like_pi(log) is False


def test_looks_like_pi_false_for_codex_shaped(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"type": "turn.started"})
        + "\n"
        + json.dumps({"type": "item.completed", "item": {"type": "command_execution"}})
        + "\n"
    )
    assert _looks_like_pi(log) is False


def test_is_pi_log_true_on_pi_fixture() -> None:
    assert _is_pi_log(PI_JSONL) is True


def test_is_pi_log_false_on_claude_fixture() -> None:
    assert _is_pi_log(CLAUDE_JSONL) is False


def test_is_pi_log_false_on_codex_fixture() -> None:
    assert _is_pi_log(CODEX_JSONL) is False


# ── detect ──


def test_detect_returns_true_on_pi_log(pi_run_dir: Path) -> None:
    extractor = PiAgentExtractor()
    assert extractor.detect(pi_run_dir) is True


def test_detect_returns_false_on_claude_log(claude_run_dir: Path) -> None:
    extractor = PiAgentExtractor()
    assert extractor.detect(claude_run_dir) is False


def test_detect_returns_false_on_codex_log(codex_run_dir: Path) -> None:
    extractor = PiAgentExtractor()
    assert extractor.detect(codex_run_dir) is False


def test_detect_returns_false_on_empty_run_dir(tmp_path: Path) -> None:
    extractor = PiAgentExtractor()
    assert extractor.detect(tmp_path) is False


# ── extract: attempt span ──


def test_extract_emits_attempt_span(pi_run_dir: Path) -> None:
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    attempts = [s for s in spans if s.kind == "attempt"]
    assert len(attempts) == 1
    a = attempts[0]
    assert a.source == "pi-agent"
    assert a.attributes["step.id"] == "sample-analysis"
    assert a.attributes["item.key"] == "ITEM-001"


def test_attempt_carries_session_id(pi_run_dir: Path) -> None:
    """session.id becomes attempt.session_id."""
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes.get("attempt.session_id") == "019e625c-8829-74e6-9951-5d11048169cf"


def test_attempt_carries_token_usage(pi_run_dir: Path) -> None:
    """agent_end.messages[].usage summed across assistant messages."""
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    # Sum from agent_end messages: 20 assistant messages
    assert attempt.attributes.get("attempt.tokens_input") == 778116
    assert attempt.attributes.get("attempt.tokens_output") == 11823
    assert attempt.attributes.get("attempt.tokens_cache_read") == 640768
    assert attempt.attributes.get("attempt.tokens_cache_write") == 0


def test_attempt_carries_model_from_agent_end(pi_run_dir: Path) -> None:
    """adapter.model should be read from agent_end messages when sidecar lacks it."""
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    # The pi fixture sidecar has no adapter_model; we read from agent_end messages
    assert attempt.attributes.get("adapter.model") == "zai-org/glm-5-maas"


def test_attempt_self_reported_cost_zero_is_a_client_estimate(pi_run_dir: Path) -> None:
    """When agent_end has cost.total, use as attempt.cost_usd with cost_is_estimated=False.

    The pi fixture has cost.total=0 on every message (glm-5 via vertex-maas
    does not charge), but the presence of the field means self-reported.
    """
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    # cost.total is present (all 0.0), so cost_is_estimated=False
    assert "attempt.cost_usd" in attempt.attributes
    assert attempt.attributes["attempt.cost_usd"] == 0.0
    assert attempt.attributes["attempt.cost_is_estimated"] is True


# ── extract: agent_session span ──


def test_extract_emits_agent_session_span(pi_run_dir: Path) -> None:
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    sessions = [s for s in spans if s.kind == "agent_session"]
    assert len(sessions) == 1
    s = sessions[0]
    assert s.attributes.get("agent.session_id") == "019e625c-8829-74e6-9951-5d11048169cf"


# ── extract: tool_call spans ──


def test_extract_emits_tool_call_spans(pi_run_dir: Path) -> None:
    """The pi fixture has 4 tool_execution_start/end pairs (all 'read')."""
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    assert len(tool_calls) == 4


def test_tool_call_carries_canonical_attributes(pi_run_dir: Path) -> None:
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    for tc in tool_calls:
        assert tc.attributes.get("step.id") == "sample-analysis"
        assert tc.attributes.get("item.key") == "ITEM-001"
        assert tc.attributes.get("tool.name") == "read"
        assert tc.attributes.get("tool.family") == "file"
        assert tc.attributes.get("tool.operation") == "read"


def test_tool_call_paired_by_tool_call_id(pi_run_dir: Path) -> None:
    """Each tool_call span should have a tool.use_id matching the toolCallId."""
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    use_ids = {tc.attributes["tool.use_id"] for tc in tool_calls}
    expected_ids = {
        "synthetic-call-22f91c38ef48",
        "synthetic-call-2cb2c86434d9",
        "synthetic-call-65766b09c26d",
        "synthetic-call-7e72399af23b",
    }
    assert use_ids == expected_ids


def test_tool_call_status_ok_when_not_error(pi_run_dir: Path) -> None:
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    for tc in tool_calls:
        assert tc.status == "ok"


# ── camelCase tool name normalization ──


def test_camelcase_tool_name_normalized_through_classification(tmp_path: Path) -> None:
    """Pi emits camelCase: webSearch, bash, read, write. Classification normalizes."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "id": "sess1"}),
                json.dumps({"type": "agent_start"}),
                json.dumps(
                    {
                        "type": "tool_execution_start",
                        "toolCallId": "c1",
                        "toolName": "webSearch",
                        "args": {"query": "ITEM-002 earnings Q2"},
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_execution_end",
                        "toolCallId": "c1",
                        "toolName": "webSearch",
                        "result": {},
                        "isError": False,
                    }
                ),
                json.dumps(
                    {
                        "type": "agent_end",
                        "messages": [
                            {
                                "role": "assistant",
                                "model": "glm-5",
                                "usage": {"input": 100, "output": 50},
                            },
                        ],
                    }
                ),
            ]
        )
        + "\n"
    )
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    assert len(tool_calls) == 1
    tc = tool_calls[0]
    assert tc.attributes["tool.name"] == "webSearch"
    assert tc.attributes["tool.family"] == "web"
    assert tc.attributes["tool.operation"] == "search"
    assert tc.attributes.get("tool.input.query") == "ITEM-002 earnings Q2"


# ── missing tool_execution_end → status=unknown ──


def test_missing_tool_execution_end_yields_unknown_status(tmp_path: Path) -> None:
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "id": "sess1"}),
                json.dumps({"type": "agent_start"}),
                json.dumps(
                    {
                        "type": "tool_execution_start",
                        "toolCallId": "c1",
                        "toolName": "bash",
                        "args": {"command": "ls"},
                    }
                ),
                # No matching tool_execution_end
                json.dumps({"type": "agent_end", "messages": []}),
            ]
        )
        + "\n"
    )
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].status == "unknown"


# ── agent_end with multiple assistant messages summed ──


def test_agent_end_sums_across_multiple_assistant_messages(tmp_path: Path) -> None:
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "id": "sess1"}),
                json.dumps({"type": "agent_start"}),
                json.dumps(
                    {
                        "type": "agent_end",
                        "messages": [
                            {
                                "role": "assistant",
                                "model": "claude-opus-4-6",
                                "usage": {
                                    "input": 1000,
                                    "output": 200,
                                    "cacheRead": 50,
                                    "cacheWrite": 10,
                                },
                            },
                            {"role": "user"},  # skipped
                            {
                                "role": "assistant",
                                "model": "claude-opus-4-6",
                                "usage": {
                                    "input": 2000,
                                    "output": 300,
                                    "cacheRead": 100,
                                    "cacheWrite": 20,
                                    "cost": {"total": 0.05},
                                },
                            },
                        ],
                    }
                ),
            ]
        )
        + "\n"
    )
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes["attempt.tokens_input"] == 3000
    assert attempt.attributes["attempt.tokens_output"] == 500
    assert attempt.attributes["attempt.tokens_cache_read"] == 150
    assert attempt.attributes["attempt.tokens_cache_write"] == 30
    assert attempt.attributes["attempt.cost_usd"] == 0.05
    assert attempt.attributes["attempt.cost_is_estimated"] is True


# ── agent_end without self-reported cost ──


def test_agent_end_without_cost_leaves_cost_absent(tmp_path: Path) -> None:
    """No cost.total in any message → no attempt.cost_usd, no cost_is_estimated."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "id": "sess1"}),
                json.dumps({"type": "agent_start"}),
                json.dumps(
                    {
                        "type": "agent_end",
                        "messages": [
                            {
                                "role": "assistant",
                                "model": "gemini-2.5-flash",
                                "usage": {"input": 500, "output": 100},
                            },
                        ],
                    }
                ),
            ]
        )
        + "\n"
    )
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert "attempt.cost_usd" not in attempt.attributes
    assert "attempt.cost_is_estimated" not in attempt.attributes


# ── adapter metadata propagation ──


def test_attempt_carries_sidecar_adapter_metadata(tmp_path: Path) -> None:
    """P0.2 metadata keys propagate to attempt span attributes."""
    tasks = tmp_path / ".logs" / "tasks" / "sample-analysis" / "ITEM-001"
    tasks.mkdir(parents=True)
    log = tasks / "log.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "id": "sess1"}),
                json.dumps({"type": "agent_start"}),
                json.dumps(
                    {
                        "type": "agent_end",
                        "messages": [
                            {
                                "role": "assistant",
                                "model": "glm-5",
                                "usage": {"input": 100, "output": 50},
                            },
                        ],
                    }
                ),
            ]
        )
        + "\n"
    )
    sidecar = log.with_suffix(".jsonl.invocation.json")
    sidecar.write_text(
        json.dumps(
            {
                "argv": ["pi", "--mode", "json"],
                "metadata": {
                    "adapter_type": "pi-cli",
                    "adapter_provider": "vertex-maas",
                    "adapter_trace_source": "pi-agent",
                    "adapter_model": "glm-5-maas",
                    "lane_id": "lane-pi-glm5",
                    "execution_profile": "synthetic-five-adapter",
                },
            }
        )
    )
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes.get("adapter.type") == "pi-cli"
    assert attempt.attributes.get("adapter.provider") == "vertex-maas"
    assert attempt.attributes.get("adapter.trace_source") == "pi-agent"
    assert attempt.attributes.get("adapter.model") == "glm-5-maas"
    assert attempt.attributes.get("lane_id") == "lane-pi-glm5"
    assert attempt.attributes.get("execution_profile") == "synthetic-five-adapter"


def test_model_from_agent_end_as_fallback_when_sidecar_lacks_it(tmp_path: Path) -> None:
    """When sidecar has no adapter_model, read from agent_end messages[0].model."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "log.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "id": "sess1"}),
                json.dumps({"type": "agent_start"}),
                json.dumps(
                    {
                        "type": "agent_end",
                        "messages": [
                            {
                                "role": "assistant",
                                "model": "gemini-2.5-flash",
                                "usage": {"input": 100, "output": 50},
                            },
                        ],
                    }
                ),
            ]
        )
        + "\n"
    )
    sidecar = log.with_suffix(".jsonl.invocation.json")
    sidecar.write_text(
        json.dumps(
            {
                "argv": ["pi", "--mode", "json"],
                "metadata": {
                    "adapter_type": "pi-cli",
                },
            }
        )
    )
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes.get("adapter.model") == "gemini-2.5-flash"


# ── tool_execution_end with isError=true ──


def test_tool_execution_end_with_is_error_true(tmp_path: Path) -> None:
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "id": "sess1"}),
                json.dumps({"type": "agent_start"}),
                json.dumps(
                    {
                        "type": "tool_execution_start",
                        "toolCallId": "c1",
                        "toolName": "bash",
                        "args": {"command": "false"},
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_execution_end",
                        "toolCallId": "c1",
                        "toolName": "bash",
                        "result": {"error": "exit code 1"},
                        "isError": True,
                    }
                ),
                json.dumps({"type": "agent_end", "messages": []}),
            ]
        )
        + "\n"
    )
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].status == "error"


# ── internal spans: compaction ──


def test_compaction_emits_internal_span(pi_run_dir: Path) -> None:
    """The pi fixture starts with a compaction event."""
    extractor = PiAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    internals = [s for s in spans if s.kind == "internal" and s.name == "compaction"]
    assert len(internals) >= 1


# ── disambiguation: claude_agent skips pi logs ──


def test_claude_agent_skips_pi_log(pi_run_dir: Path) -> None:
    """ClaudeAgentExtractor.extract must skip pi-shaped logs."""

    extractor = ClaudeAgentExtractor()
    spans = list(extractor.extract(pi_run_dir, trace_id="trace-1"))
    assert len(spans) == 0
