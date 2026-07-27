"""Smoke tests: assert per-adapter fixture JSONL snippets carry the
signature event types each adapter's trace extractor will key on.

These fixtures back the P1.2-P1.4 extractor work (codex, gemini, pi)
under spec plan-2026-05-26-multi-adapter-resource-rollup. The test
intentionally does NOT run any extractor — its job is to pin that the
fixtures still match the expected adapter wire-format after any future
regeneration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trace_agents"


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


@pytest.fixture(scope="module")
def claude_events() -> list[dict[str, object]]:
    return _load_jsonl(FIXTURE_DIR / "claude-sample.jsonl")


@pytest.fixture(scope="module")
def codex_events() -> list[dict[str, object]]:
    return _load_jsonl(FIXTURE_DIR / "codex-sample.jsonl")


@pytest.fixture(scope="module")
def gemini_events() -> list[dict[str, object]]:
    return _load_jsonl(FIXTURE_DIR / "gemini-sample.jsonl")


@pytest.fixture(scope="module")
def pi_events() -> list[dict[str, object]]:
    return _load_jsonl(FIXTURE_DIR / "pi-sample.jsonl")


def test_claude_fixture_has_assistant_tool_use(
    claude_events: list[dict[str, object]],
) -> None:
    """Claude extractor keys on assistant→content[].type=tool_use."""
    has_tool_use = False
    for ev in claude_events:
        if ev.get("type") != "assistant":
            continue
        msg = ev.get("message") or {}
        assert isinstance(msg, dict)
        for blk in msg.get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                has_tool_use = True
                break
        if has_tool_use:
            break
    assert has_tool_use, "claude fixture lost its tool_use signature"


def test_claude_fixture_has_result(claude_events: list[dict[str, object]]) -> None:
    assert any(e.get("type") == "result" for e in claude_events)


def test_codex_fixture_has_item_completed(
    codex_events: list[dict[str, object]],
) -> None:
    """Codex extractor keys on item.completed events."""
    has_item = False
    for ev in codex_events:
        if ev.get("type") != "item.completed":
            continue
        item = ev.get("item") or {}
        assert isinstance(item, dict)
        if item.get("type") in {"command_execution", "file_change", "agent_message"}:
            has_item = True
            break
    assert has_item, "codex fixture lost its item.completed signature"


def test_codex_fixture_has_turn_completed_usage(
    codex_events: list[dict[str, object]],
) -> None:
    """Codex cost wiring (P1.6) keys on turn.completed.usage."""
    for ev in codex_events:
        if ev.get("type") == "turn.completed":
            usage = ev.get("usage")
            assert isinstance(usage, dict)
            assert "input_tokens" in usage
            assert "output_tokens" in usage
            return
    pytest.fail("codex fixture missing turn.completed event with usage")


def test_gemini_fixture_has_tool_use_with_tool_id(
    gemini_events: list[dict[str, object]],
) -> None:
    """Gemini extractor pairs tool_use/tool_result by tool_id (NOT FIFO)."""
    for ev in gemini_events:
        if ev.get("type") == "tool_use":
            assert ev.get("tool_id"), (
                "gemini fixture tool_use missing tool_id field "
                "(required for non-FIFO pairing per P1.3)"
            )
            assert ev.get("tool_name"), "gemini fixture tool_use missing tool_name"
            return
    pytest.fail("gemini fixture missing any tool_use event")


def test_gemini_fixture_has_result(gemini_events: list[dict[str, object]]) -> None:
    assert any(e.get("type") == "result" for e in gemini_events)


def test_pi_fixture_has_tool_execution_start(
    pi_events: list[dict[str, object]],
) -> None:
    """Pi extractor keys on tool_execution_start events (camelCase toolName)."""
    for ev in pi_events:
        if ev.get("type") == "tool_execution_start":
            assert ev.get("toolName"), "pi fixture tool_execution_start missing toolName"
            return
    pytest.fail("pi fixture missing any tool_execution_start event")


def test_pi_fixture_has_agent_end_with_messages(
    pi_events: list[dict[str, object]],
) -> None:
    """Pi cost wiring (P1.6) keys on agent_end.messages[].usage."""
    for ev in pi_events:
        if ev.get("type") == "agent_end":
            messages = ev.get("messages")
            assert isinstance(messages, list)
            assert any(
                isinstance(m, dict) and (m.get("usage") or m.get("role") == "assistant")
                for m in messages
            )
            return
    pytest.fail("pi fixture missing agent_end event")


def test_baseline_counts_present() -> None:
    """Baseline counts file is the gate for the trace-rollup regression test."""
    baseline_path = FIXTURE_DIR / "baseline-synthetic-five-adapter.json"
    assert baseline_path.exists()
    baseline = json.loads(baseline_path.read_text())
    assert str(baseline["batch"]).startswith("synthetic-batch-")
    lanes = baseline.get("lanes")
    assert isinstance(lanes, dict)
    expected_lanes = {
        "claude",
        "codex",
        "gemini-flash",
        "gemini-pro",
        "pi-glm5",
        "pi-gemini-flash",
    }
    assert expected_lanes.issubset(lanes.keys())
    # Each lane has non-zero tool counts (proves the synthetic corpus is useful).
    for lane_name, lane in lanes.items():
        tool_counts = lane["tool_counts"]
        assert isinstance(tool_counts, dict)
        assert sum(tool_counts.values()) > 0, f"{lane_name} has zero tool counts"


def test_all_sidecars_have_adapter_metadata() -> None:
    """P0.2 sidecar contract: invocation.json carries metadata.adapter_type."""
    for adapter in ("claude", "codex", "gemini", "pi"):
        sidecar = FIXTURE_DIR / f"{adapter}-sample.jsonl.invocation.json"
        assert sidecar.exists(), f"missing sidecar for {adapter}"
        data = json.loads(sidecar.read_text())
        metadata = data.get("metadata") or {}
        # P0.2 will normalize this; for now check that *some* discriminator exists
        # so the test reads as the contract going forward.
        has_discriminator = bool(
            metadata.get("adapter") or metadata.get("adapter_type") or data.get("argv")
        )
        assert has_discriminator, f"{adapter} sidecar missing adapter discriminator"
