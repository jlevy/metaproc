"""Regression fixtures pinning the Codex trace shape.

Codex runs go through the same Claude-agent JSONL extractor; the
discriminator is the `metadata.adapter` (or `argv[0]`) entry in the
per-attempt `*.invocation.json` sidecar. These tests pin:

- the `source` flips from `claude-agent` to `codex-agent`
  (closes internal-reference — Codex failures no longer mislabel as
  `claude-agent`);
- `execution_profile` and `artifact_namespace` from the sidecar env
  carry through to the attempt span;
- adapter-neutral tool classification is independent of the source label.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.trace.extractors.claude_agent import ClaudeAgentExtractor


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "run-codex"


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _write_attempt(
    run_dir: Path,
    *,
    step: str,
    item_key: str,
    session_id: str,
    tool_use_id: str,
    tool_name: str,
    tool_input: dict[str, object],
    invocation_metadata: dict[str, Any],
) -> Path:
    """Mirror the Claude attempt fixture but stamp the sidecar with Codex metadata."""
    path = (
        run_dir
        / ".logs"
        / "tasks"
        / step
        / item_key
        / f"{step}_{item_key}_2026-05-18T00-00-00.jsonl"
    )
    _write_jsonl(
        path,
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": tool_name,
                            "input": tool_input,
                        }
                    ]
                },
                "session_id": session_id,
                "timestamp": "2026-05-18T00:00:01Z",
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "tool_use_id": tool_use_id,
                            "type": "tool_result",
                            "content": "ok",
                            "is_error": False,
                        }
                    ]
                },
                "session_id": session_id,
                "timestamp": "2026-05-18T00:00:02Z",
            },
            {
                "type": "result",
                "total_cost_usd": 0.21,
                "num_turns": 1,
                "duration_ms": 800,
                "session_id": session_id,
                "is_error": False,
            },
        ],
    )
    sidecar = path.with_suffix(".jsonl.invocation.json")
    sidecar.write_text(json.dumps(invocation_metadata))
    return path


def _codex_invocation(**env: str) -> dict[str, Any]:
    """Build a minimal Codex-shaped invocation sidecar."""
    payload: dict[str, Any] = {
        "argv": ["codex", "exec", "--profile", "codex-gpt55", "prompt"],
        "metadata": {"adapter": "codex-cli"},
    }
    if env:
        payload["env_redacted"] = env
    return payload


def test_codex_invocation_flips_source_to_codex_agent(run_dir: Path) -> None:
    _write_attempt(
        run_dir,
        step="research-step",
        item_key="CAVA",
        session_id="s-codex-1",
        tool_use_id="t1",
        tool_name="Read",
        tool_input={"file_path": "/work/bundle.json"},
        invocation_metadata=_codex_invocation(
            EXECUTION_PROFILE="codex-gpt55",
            ARTIFACT_NAMESPACE="example-artifacts-v0",
        ),
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-codex"))
    sources = {s.source for s in spans}
    assert sources == {"codex-agent"}, sources


def test_codex_attempt_carries_execution_profile_and_artifact_namespace(run_dir: Path) -> None:
    _write_attempt(
        run_dir,
        step="research-step",
        item_key="CAVA",
        session_id="s-codex-2",
        tool_use_id="t1",
        tool_name="Read",
        tool_input={"file_path": "/work/bundle.json"},
        invocation_metadata=_codex_invocation(
            EXECUTION_PROFILE="codex-gpt55",
            ARTIFACT_NAMESPACE="example-artifacts-v0",
        ),
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-codex"))
    attempt = next(s for s in spans if s.kind == "attempt")
    assert attempt.attributes["adapter.type"] == "codex-cli"
    assert attempt.attributes["execution_profile"] == "codex-gpt55"
    assert attempt.attributes["artifact_namespace"] == "example-artifacts-v0"


def test_codex_websearch_preserves_native_query(run_dir: Path) -> None:
    """Even when a Codex run happens to surface a native WebSearch tool_use
    (rare but legal — Codex shells can drive search through agent SDK paths),
    the extractor preserves `tool.input.query` and stamps `source_origin`
    the same way as the Claude fixture."""
    _write_attempt(
        run_dir,
        step="deep-research",
        item_key="MNDY",
        session_id="s-codex-5",
        tool_use_id="search1",
        tool_name="WebSearch",
        tool_input={"query": "MNDY enterprise customer growth Q2 2026"},
        invocation_metadata=_codex_invocation(),
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-codex"))
    tool = next(s for s in spans if s.kind == "tool_call")
    assert tool.source == "codex-agent"
    assert tool.attributes["tool.family"] == "web"
    assert tool.attributes["tool.operation"] == "search"
    assert tool.attributes["tool.input.query"] == "MNDY enterprise customer growth Q2 2026"
    assert tool.attributes["source_origin"] == "agent_web_search"


def test_argv_fallback_when_metadata_missing(run_dir: Path) -> None:
    """If a Codex sidecar omits `metadata.adapter`, the extractor should still
    classify the run as `codex-agent` based on `argv[0]`. This pins the
    fallback path so a sidecar-writer regression that drops the metadata block
    doesn't silently relabel runs."""
    _write_attempt(
        run_dir,
        step="research-step",
        item_key="CAVA",
        session_id="s-codex-6",
        tool_use_id="t1",
        tool_name="Read",
        tool_input={"file_path": "/work/bundle.json"},
        invocation_metadata={"argv": ["codex", "exec", "prompt"]},
    )
    spans = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-codex"))
    assert {s.source for s in spans} == {"codex-agent"}


def test_span_ids_are_deterministic_across_runs(run_dir: Path) -> None:
    """Span IDs include `source` in the hash input, so flipping from
    `claude-agent` to `codex-agent` is observable, but a second extraction
    of the same Codex run must still produce identical IDs."""
    _write_attempt(
        run_dir,
        step="research-step",
        item_key="CAVA",
        session_id="s-codex-7",
        tool_use_id="toolu_codex_1",
        tool_name="Read",
        tool_input={"file_path": "/work/bundle.json"},
        invocation_metadata=_codex_invocation(EXECUTION_PROFILE="codex-gpt55"),
    )
    first = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-codex"))
    second = list(ClaudeAgentExtractor().extract(run_dir, trace_id="run-codex"))
    assert [s.span_id for s in first] == [s.span_id for s in second]
