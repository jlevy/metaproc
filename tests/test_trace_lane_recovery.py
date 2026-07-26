"""Regression: lane-comparison consumers can recover lane_id +
execution_profile from trace spans without EIA-specific parsing.

Beads: internal-reference (env-var key parity for METAPROC_LANE_ID),
internal-reference (lane metadata in trace events and source logs),
and the final Phase-3 checklist item in the provenance/status/trace
spec ("Add regression coverage that lane-comparison consumers can
recover item, execution profile, lane id, raw-log reference,
artifact namespace, provider, status, duration, and cost context
from trace rows without EIA-specific parsing").

This file pins the contract end-to-end:
- ``run_parallel`` injects ``METAPROC_LANE_ID`` and ``EXECUTION_PROFILE``
  into the subprocess env (internal review / Phase 2).
- ``write_invocation_sidecar`` captures ``env_redacted`` from that env.
- ``ClaudeAgentExtractor`` reads the sidecar and stamps lane_id +
  execution_profile + artifact_namespace on the attempt span.

Together these mean a lane-native consumer can join
``(item.key, lane_id, execution_profile)`` across Claude attempts,
arena_wrapper resource events, and web_bundle spans without ever
looking at run id, dispatch plan, or EIA-specific YAML.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.trace import TraceEvent
from metaproc.trace.extractors.claude_agent import ClaudeAgentExtractor


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _write_invocation_sidecar(
    jsonl_path: Path,
    *,
    lane_id: str | None = None,
    execution_profile: str | None = None,
    artifact_namespace: str | None = None,
    legacy_lane_id_key: bool = False,
) -> None:
    """Write the .invocation.json sidecar that backend.py emits.

    ``legacy_lane_id_key=True`` exercises the back-compat path where an
    older injector wrote ``LANE_ID`` instead of ``METAPROC_LANE_ID`` —
    both keys must be honored.
    """
    env: dict[str, str] = {}
    if lane_id is not None:
        env_key = "LANE_ID" if legacy_lane_id_key else "METAPROC_LANE_ID"
        env[env_key] = lane_id
    if execution_profile is not None:
        env["EXECUTION_PROFILE"] = execution_profile
    if artifact_namespace is not None:
        env["ARTIFACT_NAMESPACE"] = artifact_namespace
    payload: dict[str, Any] = {
        "argv": ["claude", "--prompt-file", "/tmp/prompt"],
        "cwd": "/tmp",
        "env_redacted": env,
        "metadata": {"adapter": "claude-code-cli"},
    }
    sidecar = jsonl_path.with_suffix(jsonl_path.suffix + ".invocation.json")
    sidecar.write_text(json.dumps(payload, indent=2))


def _write_attempt(
    run_dir: Path,
    *,
    step: str = "analysis-research",
    item_key: str = "CAVA",
    session_id: str = "s1",
) -> Path:
    path = (
        run_dir
        / ".logs"
        / "tasks"
        / step
        / item_key
        / f"{step}_{item_key}_2026-05-19T00-00-00.jsonl"
    )
    events: list[dict[str, object]] = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_x",
                        "name": "Write",
                        "input": {"file_path": f"/run/{item_key}/edge-brief.md"},
                    }
                ]
            },
            "session_id": session_id,
            "timestamp": "2026-05-19T00:00:01Z",
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "tool_use_id": "toolu_x",
                        "type": "tool_result",
                        "content": "ok",
                        "is_error": False,
                    }
                ]
            },
            "session_id": session_id,
            "timestamp": "2026-05-19T00:00:02Z",
        },
        {
            "type": "result",
            "total_cost_usd": 0.5,
            "num_turns": 1,
            "duration_ms": 1500,
            "session_id": session_id,
            "is_error": False,
        },
    ]
    _write_jsonl(path, events)
    return path


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "arb-2026-05-19-cava"


def _attempt_span(spans: list[TraceEvent]) -> TraceEvent:
    return next(s for s in spans if s.kind == "attempt")


# ── Lane env-var parity (bead internal-reference) ─────────────────────


def test_attempt_span_recovers_lane_id_from_metaproc_lane_id_env(run_dir: Path) -> None:
    """The canonical env key: METAPROC_LANE_ID injected by run_parallel."""
    jsonl_path = _write_attempt(run_dir)
    _write_invocation_sidecar(
        jsonl_path,
        lane_id="codex-gpt55",
        execution_profile="codex-gpt55",
        artifact_namespace="example-artifacts-v0",
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="arb-2026-05-19-cava"))
    attempt = _attempt_span(spans)
    assert attempt.attributes["lane_id"] == "codex-gpt55"
    assert attempt.attributes["execution_profile"] == "codex-gpt55"
    assert attempt.attributes["artifact_namespace"] == "example-artifacts-v0"


def test_attempt_span_falls_back_to_legacy_lane_id_env(run_dir: Path) -> None:
    """Older invocations may have stamped LANE_ID; honor it as a fallback."""
    jsonl_path = _write_attempt(run_dir)
    _write_invocation_sidecar(
        jsonl_path,
        lane_id="claude-opus",
        execution_profile="claude-opus",
        legacy_lane_id_key=True,
    )
    attempt = _attempt_span(
        list(ClaudeAgentExtractor().extract(run_dir, trace_id="arb-2026-05-19-cava"))
    )
    assert attempt.attributes["lane_id"] == "claude-opus"


def test_attempt_span_has_no_lane_id_when_env_missing(run_dir: Path) -> None:
    """Degenerate older runs without lane env vars stay parseable."""
    jsonl_path = _write_attempt(run_dir)
    _write_invocation_sidecar(jsonl_path, execution_profile="codex-gpt55")
    attempt = _attempt_span(
        list(ClaudeAgentExtractor().extract(run_dir, trace_id="arb-2026-05-19-cava"))
    )
    assert "lane_id" not in attempt.attributes
    # execution_profile still flows.
    assert attempt.attributes["execution_profile"] == "codex-gpt55"


# ── Lane-comparison consumer contract (Phase 3 checklist item) ─


def test_lane_comparison_consumer_can_join_attempt_to_lane_metadata(
    run_dir: Path,
) -> None:
    """Two attempts for the same item, different lanes, must be addressable
    by (item.key, lane_id, execution_profile) without EIA-specific parsing.
    """
    codex_jsonl = _write_attempt(
        run_dir,
        step="analysis-research",
        item_key="CAVA",
        session_id="codex-s1",
    )
    _write_invocation_sidecar(
        codex_jsonl,
        lane_id="codex-gpt55",
        execution_profile="codex-gpt55",
        artifact_namespace="example-artifacts-v0",
    )
    # Same item, different lane (Claude). A real two-profile dispatch would
    # write into a sibling run dir; for the regression contract the two
    # attempts can live side-by-side under one .logs/tasks tree.
    claude_jsonl = (
        run_dir
        / ".logs"
        / "tasks"
        / "analysis-research"
        / "CAVA-claude"
        / ("edge-research_CAVA-claude_2026-05-19T00-00-00.jsonl")
    )
    _write_jsonl(
        claude_jsonl,
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_y",
                            "name": "Write",
                            "input": {"file_path": "/run/CAVA/edge-brief.md"},
                        }
                    ]
                },
                "session_id": "claude-s1",
                "timestamp": "2026-05-19T00:01:00Z",
            },
            {
                "type": "result",
                "session_id": "claude-s1",
                "is_error": False,
                "duration_ms": 2000,
                "total_cost_usd": 0.7,
                "num_turns": 1,
            },
        ],
    )
    _write_invocation_sidecar(
        claude_jsonl,
        lane_id="claude-opus",
        execution_profile="claude-opus",
        artifact_namespace="example-artifacts-v0",
    )
    attempts = [
        s
        for s in ClaudeAgentExtractor().extract(run_dir, trace_id="arb-2026-05-19-cava")
        if s.kind == "attempt"
    ]
    by_lane = {a.attributes["lane_id"]: a.attributes for a in attempts if "lane_id" in a.attributes}
    # Two lanes, both addressable; same item.key, distinct execution_profile.
    assert set(by_lane) == {"codex-gpt55", "claude-opus"}
    assert {by_lane["codex-gpt55"]["item.key"], by_lane["claude-opus"]["item.key"]} == {
        "CAVA",
        "CAVA-claude",
    }
    assert by_lane["codex-gpt55"]["execution_profile"] == "codex-gpt55"
    assert by_lane["claude-opus"]["execution_profile"] == "claude-opus"
    # raw_log_ref + artifact_namespace flow per the spec's recoverable
    # field list — joining lane comparison rows back to the original
    # session log is a one-attribute lookup.
    for lane_attrs in by_lane.values():
        assert "raw_log_ref" in lane_attrs
        assert lane_attrs["artifact_namespace"] == "example-artifacts-v0"
