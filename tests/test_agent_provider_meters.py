"""Exact and explicitly unmeasured built-in agent provider meters."""

from __future__ import annotations

from metaproc.logutil.agent_provider_meters import extract_agent_provider_evidence
from metaproc.logutil.parsing import LogEvent
from metaproc.models.resources import CoverageState


def _raw(adapter: str, payload: dict[str, object], *, done: bool = False) -> LogEvent:
    return LogEvent(
        kind="result" if done else "system",
        summary="event",
        adapter=adapter,
        is_done=done,
        raw=payload,
    )


def _by_meter(evidence):
    return {quantity.key.meter: quantity for quantity in evidence.meters}


def test_claude_preserves_exact_zero_turn_and_server_tool_counts() -> None:
    evidence = extract_agent_provider_evidence(
        adapter="claude",
        model="claude-opus",
        events=[
            _raw(
                "claude",
                {
                    "type": "result",
                    "num_turns": 0,
                    "usage": {
                        "server_tool_use": {
                            "web_search_requests": 0,
                            "web_fetch_requests": 0,
                        }
                    },
                },
                done=True,
            )
        ],
    )

    assert evidence is not None
    meters = _by_meter(evidence)
    assert meters["agent_invocations"].actual_quantity == 1
    assert meters["model_turns"].actual_quantity == 0
    assert meters["server_web_search_requests"].actual_quantity == 0
    assert meters["server_web_fetch_requests"].actual_quantity == 0
    assert meters["api_requests"].coverage is CoverageState.UNMEASURED


def test_codex_failed_turn_still_has_exact_terminal_and_turn_boundaries() -> None:
    evidence = extract_agent_provider_evidence(
        adapter="codex",
        model="gpt-5",
        events=[
            _raw("codex", {"type": "turn.started"}),
            _raw("codex", {"type": "turn.failed"}, done=True),
        ],
    )

    assert evidence is not None
    meters = _by_meter(evidence)
    assert meters["agent_invocations"].actual_quantity == 1
    assert meters["model_turns"].actual_quantity == 1
    assert meters["api_requests"].coverage is CoverageState.UNMEASURED


def test_multiple_codex_turns_are_one_agent_invocation() -> None:
    evidence = extract_agent_provider_evidence(
        adapter="codex",
        model="gpt-5",
        events=[
            _raw("codex", {"type": "turn.started"}),
            _raw("codex", {"type": "turn.completed"}, done=True),
            _raw("codex", {"type": "turn.started"}),
            _raw("codex", {"type": "turn.completed"}, done=True),
        ],
    )

    assert evidence is not None
    meters = _by_meter(evidence)
    assert meters["agent_invocations"].actual_quantity == 1
    assert meters["model_turns"].actual_quantity == 2


def test_gemini_turns_and_api_requests_remain_unmeasured() -> None:
    evidence = extract_agent_provider_evidence(
        adapter="gemini",
        model="gemini-pro",
        events=[_raw("gemini", {"type": "result"}, done=True)],
    )

    assert evidence is not None
    meters = _by_meter(evidence)
    assert meters["agent_invocations"].actual_quantity == 1
    assert meters["model_turns"].coverage is CoverageState.UNMEASURED
    assert meters["api_requests"].coverage is CoverageState.UNMEASURED


def test_granular_web_tool_does_not_become_a_provider_request() -> None:
    evidence = extract_agent_provider_evidence(
        adapter="claude",
        model="claude-opus",
        events=[
            LogEvent(
                kind="tool_call",
                summary="[call:WebSearch]",
                adapter="claude",
                tool_name="WebSearch",
                invocation_id="tool-1",
            ),
            _raw("claude", {"type": "result", "num_turns": 1}, done=True),
        ],
    )

    assert evidence is not None
    meters = _by_meter(evidence)
    assert meters["server_web_search_requests"].coverage is CoverageState.UNMEASURED
    assert meters["api_requests"].coverage is CoverageState.UNMEASURED


def test_partial_claude_server_meter_is_explicitly_unmeasured() -> None:
    evidence = extract_agent_provider_evidence(
        adapter="claude",
        model="claude-opus",
        events=[
            _raw(
                "claude",
                {
                    "type": "result",
                    "num_turns": 1,
                    "usage": {"server_tool_use": {"web_search_requests": 2}},
                },
                done=True,
            ),
            _raw("claude", {"type": "result", "num_turns": 1}, done=True),
        ],
    )

    assert evidence is not None
    meters = _by_meter(evidence)
    assert meters["server_web_search_requests"].coverage is CoverageState.UNMEASURED


def test_no_terminal_boundary_produces_no_invocation_evidence() -> None:
    assert (
        extract_agent_provider_evidence(
            adapter="claude",
            model="claude-opus",
            events=[_raw("claude", {"type": "system"})],
        )
        is None
    )
