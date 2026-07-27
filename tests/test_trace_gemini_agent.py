"""P1.3 -- Gemini CLI trace extractor.

Gemini-cli emits flat tool_use/tool_result events paired by tool_id
(NOT FIFO). Results return out of order when the scheduler runs
parallelizable tools via Promise.all.

These tests use the real fixture at
``metaproc/tests/fixtures/trace_agents/gemini-sample.jsonl`` as the
primary corpus, plus synthetic logs for edge cases (parallel out-of-order
results, unpaired starts, disambiguation).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.trace.extractors.gemini_agent import (
    GeminiAgentExtractor,
    _is_gemini_log,
    _looks_like_gemini,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trace_agents"
GEMINI_JSONL = FIXTURE_DIR / "gemini-sample.jsonl"
GEMINI_SIDECAR = FIXTURE_DIR / "gemini-sample.jsonl.invocation.json"
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
def gemini_run_dir(tmp_path: Path) -> Path:
    """Build a synthetic run-dir from the gemini fixture."""
    run_dir = tmp_path / "run-gemini"
    tasks = run_dir / ".logs" / "tasks" / "sample-analysis" / "ITEM-001"
    tasks.mkdir(parents=True)
    log = tasks / "sample-analysis_ITEM-001_2030-01-01T06-09-40.jsonl"
    log.write_bytes(GEMINI_JSONL.read_bytes())
    sidecar = tasks / (log.name + ".invocation.json")
    sidecar.write_bytes(GEMINI_SIDECAR.read_bytes())
    return run_dir


@pytest.fixture
def claude_run_dir(tmp_path: Path) -> Path:
    """Build a synthetic run-dir with a claude log (gemini extractor must NOT claim)."""
    run_dir = tmp_path / "run-claude"
    tasks = run_dir / ".logs" / "tasks" / "sample-analysis" / "ITEM-002"
    tasks.mkdir(parents=True)
    log = tasks / "sample-analysis_ITEM-002.jsonl"
    log.write_bytes(CLAUDE_JSONL.read_bytes())
    return run_dir


@pytest.fixture
def codex_run_dir(tmp_path: Path) -> Path:
    """Build a synthetic run-dir with a codex log (gemini extractor must NOT claim)."""
    run_dir = tmp_path / "run-codex"
    tasks = run_dir / ".logs" / "tasks" / "edge-candidate-ledger" / "MOD"
    tasks.mkdir(parents=True)
    log = tasks / "edge-candidate-ledger_MOD.jsonl"
    log.write_bytes(CODEX_JSONL.read_bytes())
    return run_dir


# -- detect --


def test_detect_returns_true_on_gemini_log(gemini_run_dir: Path) -> None:
    extractor = GeminiAgentExtractor()
    assert extractor.detect(gemini_run_dir) is True


def test_detect_returns_false_on_claude_log(claude_run_dir: Path) -> None:
    extractor = GeminiAgentExtractor()
    assert extractor.detect(claude_run_dir) is False


def test_detect_returns_false_on_codex_log(codex_run_dir: Path) -> None:
    extractor = GeminiAgentExtractor()
    assert extractor.detect(codex_run_dir) is False


def test_detect_returns_false_on_empty_run_dir(tmp_path: Path) -> None:
    extractor = GeminiAgentExtractor()
    assert extractor.detect(tmp_path) is False


def test_looks_like_gemini_recognizes_flat_tool_use_with_tool_id(tmp_path: Path) -> None:
    """Schema sniff key: flat tool_use with top-level tool_id."""
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"type": "init", "model": "gemini-3.5-flash"})
        + "\n"
        + json.dumps(
            {
                "type": "tool_use",
                "tool_name": "read_file",
                "tool_id": "read_file_123_0",
                "parameters": {"file_path": "/foo.md"},
            }
        )
        + "\n"
    )
    assert _looks_like_gemini(log) is True


def test_looks_like_gemini_returns_false_for_claude_shaped(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"type": "system", "subtype": "init"})
        + "\n"
        + json.dumps({"type": "assistant", "message": {"content": []}})
        + "\n"
    )
    assert _looks_like_gemini(log) is False


def test_looks_like_gemini_returns_false_for_codex_shaped(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"type": "turn.started"})
        + "\n"
        + json.dumps({"type": "item.completed", "item": {"type": "command_execution"}})
        + "\n"
    )
    assert _looks_like_gemini(log) is False


# -- extract: attempt span --


def test_extract_emits_attempt_span(gemini_run_dir: Path) -> None:
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    attempts = [s for s in spans if s.kind == "attempt"]
    assert len(attempts) == 1
    a = attempts[0]
    assert a.source == "gemini-agent"
    assert a.attributes["step.id"] == "sample-analysis"
    assert a.attributes["item.key"] == "ITEM-001"


def test_attempt_carries_token_usage(gemini_run_dir: Path) -> None:
    """Token usage from result.stats.models.{model}.{input_tokens,output_tokens,cached}."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    # From the fixture: two models, summed.
    assert attempt.attributes.get("attempt.tokens_input") == 1643732
    assert attempt.attributes.get("attempt.tokens_output") == 11633
    assert attempt.attributes.get("attempt.tokens_cached") == 960013


def test_attempt_carries_session_id(gemini_run_dir: Path) -> None:
    """init.session_id becomes attempt.session_id."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes.get("attempt.session_id") == "5f60eae2-8c7a-4cfb-84dd-9cfce2891c68"


def test_attempt_carries_model(gemini_run_dir: Path) -> None:
    """init.model becomes adapter.model."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes.get("adapter.model") == "gemini-3.5-flash"


def test_attempt_status_is_ok_on_success(gemini_run_dir: Path) -> None:
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.status == "ok"


def test_attempt_duration_from_result_stats(gemini_run_dir: Path) -> None:
    """duration_ms comes from result.stats.duration_ms."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.duration_ms == 335167.0


# -- extract: session span --


def test_extract_emits_session_span(gemini_run_dir: Path) -> None:
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    sessions = [s for s in spans if s.kind == "agent_session"]
    assert len(sessions) == 1
    sess = sessions[0]
    assert sess.attributes.get("agent.session_id") == "5f60eae2-8c7a-4cfb-84dd-9cfce2891c68"
    assert sess.status == "ok"


# -- extract: tool_call spans --


def test_extract_emits_tool_call_spans(gemini_run_dir: Path) -> None:
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    # The fixture has: update_topic, read_file x2, run_shell_command x4, list_directory
    assert len(tool_calls) >= 7


def test_read_file_classified_as_file_read(gemini_run_dir: Path) -> None:
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    reads = [
        s for s in spans if s.kind == "tool_call" and s.attributes.get("tool.name") == "read_file"
    ]
    assert len(reads) >= 1
    for s in reads:
        assert s.attributes.get("tool.family") == "file"
        assert s.attributes.get("tool.operation") == "read"


def test_run_shell_command_classified_as_shell(gemini_run_dir: Path) -> None:
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    shell_spans = [
        s
        for s in spans
        if s.kind == "tool_call" and s.attributes.get("tool.name") == "run_shell_command"
    ]
    assert len(shell_spans) >= 1
    for s in shell_spans:
        assert s.attributes.get("tool.family") == "shell"
        assert s.attributes.get("tool.operation") == "execute"


def test_tool_result_status_maps_to_span_status(gemini_run_dir: Path) -> None:
    """tool_result.status='success' -> ok, 'error' -> error."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]

    # In the fixture, read_file calls return status=error (path not in workspace).
    error_spans = [s for s in tool_calls if s.status == "error"]
    ok_spans = [s for s in tool_calls if s.status == "ok"]
    assert len(error_spans) >= 2  # The two read_file errors
    assert len(ok_spans) >= 4  # update_topic + shell commands


def test_tool_use_carries_tool_id(gemini_run_dir: Path) -> None:
    """Each tool_call span carries the tool_id from the raw event."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    for tc in tool_calls:
        assert isinstance(tc.attributes.get("tool.use_id"), str)
        assert len(tc.attributes["tool.use_id"]) > 0


# -- pairing by tool_id: out-of-order results --


def test_parallel_results_out_of_order_paired_by_tool_id(tmp_path: Path) -> None:
    """Gemini runs parallel tools and results come back out of order.

    Emit two tool_use events (A, B), then results in reverse order (B, A).
    Assert each result pairs with the correct tool_use by tool_id.
    """
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "init",
                        "model": "gemini-3.5-flash",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "session_id": "s1",
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_use",
                        "tool_name": "google_web_search",
                        "tool_id": "search_A",
                        "parameters": {"query": "alpha"},
                        "timestamp": "2026-01-01T00:00:01Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_use",
                        "tool_name": "google_web_search",
                        "tool_id": "search_B",
                        "parameters": {"query": "beta"},
                        "timestamp": "2026-01-01T00:00:01Z",
                    }
                ),
                # Results in REVERSE order
                json.dumps(
                    {
                        "type": "tool_result",
                        "tool_id": "search_B",
                        "status": "success",
                        "output": "result B",
                        "timestamp": "2026-01-01T00:00:03Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_result",
                        "tool_id": "search_A",
                        "status": "error",
                        "output": "result A failed",
                        "timestamp": "2026-01-01T00:00:04Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "timestamp": "2026-01-01T00:00:05Z",
                        "stats": {"models": {}},
                    }
                ),
            ]
        )
        + "\n"
    )
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    assert len(tool_calls) == 2

    by_id = {s.attributes["tool.use_id"]: s for s in tool_calls}
    # search_A got the error result
    assert by_id["search_A"].status == "error"
    assert by_id["search_A"].attributes.get("tool.input.query") == "alpha"
    # search_B got the success result
    assert by_id["search_B"].status == "ok"
    assert by_id["search_B"].attributes.get("tool.input.query") == "beta"


def test_google_web_search_classified_as_web_search(tmp_path: Path) -> None:
    """google_web_search maps to tool.family=web, operation=search."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "init",
                        "model": "gemini-3.5-flash",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "session_id": "s1",
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_use",
                        "tool_name": "google_web_search",
                        "tool_id": "ws_1",
                        "parameters": {"query": "ITEM-002 earnings Q2"},
                        "timestamp": "2026-01-01T00:00:01Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_result",
                        "tool_id": "ws_1",
                        "status": "success",
                        "output": "search results",
                        "timestamp": "2026-01-01T00:00:02Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "timestamp": "2026-01-01T00:00:03Z",
                        "stats": {"models": {}},
                    }
                ),
            ]
        )
        + "\n"
    )
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    web_spans = [
        s for s in spans if s.kind == "tool_call" and s.attributes.get("tool.family") == "web"
    ]
    assert len(web_spans) == 1
    ws = web_spans[0]
    assert ws.attributes.get("tool.operation") == "search"
    assert ws.attributes.get("tool.input.query") == "ITEM-002 earnings Q2"
    assert ws.attributes.get("source_origin") == "agent_web_search"


def test_web_fetch_classified_as_web_fetch(tmp_path: Path) -> None:
    """web_fetch maps to tool.family=web, operation=fetch."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "init",
                        "model": "gemini-3.5-flash",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "session_id": "s1",
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_use",
                        "tool_name": "web_fetch",
                        "tool_id": "wf_1",
                        "parameters": {"url": "https://example.com/data"},
                        "timestamp": "2026-01-01T00:00:01Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_result",
                        "tool_id": "wf_1",
                        "status": "success",
                        "output": "fetched content",
                        "timestamp": "2026-01-01T00:00:02Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "timestamp": "2026-01-01T00:00:03Z",
                        "stats": {"models": {}},
                    }
                ),
            ]
        )
        + "\n"
    )
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    web_spans = [
        s for s in spans if s.kind == "tool_call" and s.attributes.get("tool.family") == "web"
    ]
    assert len(web_spans) == 1
    wf = web_spans[0]
    assert wf.attributes.get("tool.operation") == "fetch"
    assert wf.attributes.get("tool.input.url") == "https://example.com/data"


# -- unpaired tool_use events --


def test_unpaired_tool_use_gets_status_unknown(tmp_path: Path) -> None:
    """A tool_use with no matching tool_result should get status=unknown."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "init",
                        "model": "gemini-3.5-flash",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "session_id": "s1",
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_use",
                        "tool_name": "read_file",
                        "tool_id": "rf_orphan",
                        "parameters": {"file_path": "/missing.txt"},
                        "timestamp": "2026-01-01T00:00:01Z",
                    }
                ),
                # No tool_result for rf_orphan
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "timestamp": "2026-01-01T00:00:05Z",
                        "stats": {"models": {}},
                    }
                ),
            ]
        )
        + "\n"
    )
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].status == "unknown"
    assert tool_calls[0].attributes["tool.use_id"] == "rf_orphan"


# -- terminal stats --


def test_terminal_stats_token_aggregation(tmp_path: Path) -> None:
    """Token usage from result.stats.models aggregates across multiple models."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "init",
                        "model": "gemini-3.5-flash",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "session_id": "s1",
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "timestamp": "2026-01-01T00:00:10Z",
                        "stats": {
                            "duration_ms": 5000,
                            "models": {
                                "model-a": {"input_tokens": 100, "output_tokens": 50, "cached": 10},
                                "model-b": {"input_tokens": 200, "output_tokens": 75, "cached": 0},
                            },
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes["attempt.tokens_input"] == 300
    assert attempt.attributes["attempt.tokens_output"] == 125
    assert attempt.attributes["attempt.tokens_cached"] == 10
    assert attempt.duration_ms == 5000.0


# -- sidecar metadata propagation --


def test_attempt_carries_sidecar_adapter_metadata(tmp_path: Path) -> None:
    """P0.2 metadata keys propagate to attempt span attributes."""
    tasks = tmp_path / ".logs" / "tasks" / "sample-analysis" / "ITEM-001"
    tasks.mkdir(parents=True)
    log = tasks / "log.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "init",
                        "model": "gemini-3.5-flash",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "session_id": "s1",
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "status": "success",
                        "timestamp": "2026-01-01T00:00:10Z",
                        "stats": {"models": {}},
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
                "argv": ["gemini", "-m", "gemini-3.5-flash"],
                "metadata": {
                    "adapter_type": "gemini-cli",
                    "adapter_provider": "google",
                    "adapter_trace_source": "gemini-agent",
                    "adapter_model": "gemini-3.5-flash",
                    "lane_id": "gemini-flash",
                    "execution_profile": "synthetic-five-adapter",
                },
            }
        )
    )
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes.get("adapter.type") == "gemini-cli"
    assert attempt.attributes.get("adapter.provider") == "google"
    assert attempt.attributes.get("adapter.trace_source") == "gemini-agent"
    assert attempt.attributes.get("adapter.model") == "gemini-3.5-flash"
    assert attempt.attributes.get("lane_id") == "gemini-flash"
    assert attempt.attributes.get("execution_profile") == "synthetic-five-adapter"


# -- sidecar from real fixture --


def test_real_fixture_sidecar_lane_id(gemini_run_dir: Path) -> None:
    """The real sidecar fixture carries lane metadata via env vars."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    attempt = next(s for s in spans if s.kind == "attempt")
    # The real fixture's sidecar has metadata but no adapter_type/lane_id
    # keys at the P0.2 level (it pre-dates P0.2); this test documents the
    # current behavior. Once P0.2 sidecars ship, the assertion should
    # be that lane_id is populated.
    # For now, just ensure the extractor ran without errors.
    assert attempt.attributes.get("step.id") == "sample-analysis"


# -- disambiguation --


def test_is_gemini_log_true_for_gemini_shape(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"type": "init", "model": "gemini-3.5-flash"})
        + "\n"
        + json.dumps(
            {
                "type": "tool_use",
                "tool_name": "read_file",
                "tool_id": "rf_1",
                "parameters": {},
            }
        )
        + "\n"
    )
    assert _is_gemini_log(log) is True


def test_is_gemini_log_false_for_claude_shape(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(json.dumps({"type": "assistant", "message": {}}) + "\n")
    assert _is_gemini_log(log) is False


def test_is_gemini_log_false_for_codex_shape(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(json.dumps({"type": "turn.completed", "usage": {}}) + "\n")
    assert _is_gemini_log(log) is False


# -- compaction internal span --


def test_compaction_emits_internal_span(gemini_run_dir: Path) -> None:
    """The gemini fixture has a compaction event that should emit an internal span."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    compaction_spans = [s for s in spans if s.kind == "internal" and s.name == "compaction"]
    assert len(compaction_spans) >= 1
    c = compaction_spans[0]
    assert c.attributes.get("internal.kind") == "compaction"
    assert c.attributes.get("internal.original_size") == 69603


# -- error attempt --


def test_missing_result_event_gives_error_status(tmp_path: Path) -> None:
    """If there is no result event, the attempt status should be error."""
    tasks = tmp_path / ".logs" / "tasks" / "step" / "item"
    tasks.mkdir(parents=True)
    log = tasks / "x.jsonl"
    log.write_text(
        json.dumps(
            {
                "type": "tool_use",
                "tool_name": "read_file",
                "tool_id": "rf_1",
                "parameters": {},
                "timestamp": "2026-01-01T00:00:01Z",
            }
        )
        + "\n"
    )
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(tmp_path, trace_id="t"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.status == "error"
    assert attempt.status_derived is True


# -- canonical attributes on every tool_call span --


def test_all_tool_calls_carry_canonical_attributes(gemini_run_dir: Path) -> None:
    """Cross-adapter parity: every tool_call has step.id, item.key, tool.name, tool.use_id."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    assert len(tool_calls) >= 1
    for tc in tool_calls:
        assert isinstance(tc.attributes.get("step.id"), str)
        assert isinstance(tc.attributes.get("item.key"), str)
        assert isinstance(tc.attributes.get("tool.name"), str)
        assert isinstance(tc.attributes.get("tool.use_id"), str)
        assert tc.source == "gemini-agent"


# -- fixture parity: the last event in the fixture should be a list_directory --


def test_fixture_last_tool_is_list_directory(gemini_run_dir: Path) -> None:
    """The fixture's last non-result tool event is list_directory. Verify it's extracted."""
    extractor = GeminiAgentExtractor()
    spans = list(extractor.extract(gemini_run_dir, trace_id="trace-1"))
    tool_calls = [s for s in spans if s.kind == "tool_call"]
    names = [s.attributes.get("tool.name") for s in tool_calls]
    assert "list_directory" in names
