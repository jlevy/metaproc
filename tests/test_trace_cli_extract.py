"""Integration test for ``metaproc trace --extract`` and the runner."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.paths import legacy_trace_out
from metaproc.trace.runner import extract_trace
from metaproc.trace.store import read_trace, trace_path


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """A minimal run dir with one Claude attempt, one arena tool call,
    one web bundle, plus DAG + result.yaml state.
    """
    d = tmp_path / "synthetic-trace-run"
    # Engine process events
    (d / ".logs").mkdir(parents=True)
    with (d / ".logs" / "process-events.jsonl").open("w") as f:
        f.write(
            json.dumps(
                {
                    "ts": "2026-05-12T00:00:00Z",
                    "event": "process_start",
                    "process": "analysis-research",
                    "run_id": "synthetic-trace-run",
                    "backend": "local",
                    "step_count": 1,
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "ts": "2026-05-12T00:00:01Z",
                    "event": "step_start",
                    "step_id": "research-step",
                    "mode": "agent",
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "ts": "2026-05-12T00:00:10Z",
                    "event": "step_complete",
                    "step_id": "research-step",
                    "elapsed_s": 9.0,
                }
            )
            + "\n"
        )

    # Item result.yaml
    item_dir = d / ".state" / "tasks" / "research-step" / "MNDY"
    item_dir.mkdir(parents=True)
    (item_dir / "result.yaml").write_text(
        yaml.safe_dump(
            {
                "run_id": "synthetic-trace-run",
                "step_id": "research-step",
                "state": "completed",
                "validated": True,
                "published_at": "2026-05-12T00:00:10",
            }
        )
    )

    # Claude attempt
    jsonl = d / ".logs" / "tasks" / "research-step" / "MNDY" / "x.jsonl"
    jsonl.parent.mkdir(parents=True)
    with jsonl.open("w") as f:
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Read",
                                "input": {"file_path": "/x.md"},
                            }
                        ]
                    },
                    "session_id": "s1",
                    "timestamp": "2026-05-12T00:00:02Z",
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "tool_use_id": "t1",
                                "type": "tool_result",
                                "content": "ok",
                                "is_error": False,
                            }
                        ]
                    },
                    "session_id": "s1",
                    "timestamp": "2026-05-12T00:00:03Z",
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 0.5,
                    "num_turns": 1,
                    "duration_ms": 1000,
                    "session_id": "s1",
                    "is_error": False,
                }
            )
            + "\n"
        )

    # Arena wrapper log
    arena_log = d / "analysis-research" / ".logs" / "tools" / "arena" / "invocations.jsonl"
    arena_log.parent.mkdir(parents=True)
    with arena_log.open("w") as f:
        f.write(json.dumps({"type": "config", "mode": "live"}) + "\n")
        f.write(
            json.dumps(
                {
                    "timestamp": "2026-05-12T00:00:05Z",
                    "tool_name": "trends",
                    "tier": "live",
                    "command": ["arena", "tool", "trends", "MNDY"],
                    "extra_args": [],
                    "exit_code": 0,
                    "duration_s": 1.5,
                    "error": None,
                }
            )
            + "\n"
        )

    # Web bundle JSON
    bundle = d / "analysis-research" / "v0" / "MNDY" / "web-research-bundle.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_text(
        json.dumps(
            {
                "success": True,
                "bundle": {
                    "ticker": "MNDY",
                    "providers": ["exa", "perplexity"],
                    "execution": {
                        "executedAt": "2026-05-12T00:00:00Z",
                        "completedAt": "2026-05-12T00:01:00Z",
                        "durationMs": 60000,
                    },
                    "queries": [
                        {
                            "id": "q001",
                            "byProvider": {
                                "exa": {
                                    "status": "success",
                                    "latencyMs": 100,
                                    "urlCount": 5,
                                    "costUsd": 0.01,
                                },
                                "perplexity": {
                                    "status": "error",
                                    "latencyMs": 50,
                                    "urlCount": 0,
                                    "costUsd": 0,
                                    "error": {"type": "api_auth_failed", "message": "401"},
                                },
                            },
                        }
                    ],
                    "summary": {
                        "skippedQueries": [],
                        "cost": {
                            "byProvider": {
                                "exa": {"usd": 0.01, "source": "provider_returned", "requests": 1},
                                "perplexity": {"usd": 0.0, "source": "unavailable", "requests": 0},
                            }
                        },
                    },
                },
            }
        )
    )
    return d


def test_extract_writes_trace_file(run_dir: Path):
    """Confirm the bundled framework extractors fire on the synthetic
    run-dir. Consumer-specific tool extractors
    register via entry points from ``example_plugin``; this
    test stays inside metaproc and only asserts on what the framework
    ships by default.
    """
    spans = extract_trace(run_dir)
    kinds = {s.kind for s in spans}
    # Framework-bundled extractors (metaproc-engine + claude-agent)
    # produce these kinds; if either is missing the framework itself
    # is broken.
    assert {
        "run",
        "step",
        "item",
        "attempt",
        "agent_session",
        "tool_call",
    }.issubset(kinds), f"missing framework kinds: {kinds}"
    assert spans[0].trace_id == "synthetic-trace-run"


def test_extract_includes_extractor_sources(run_dir: Path):
    """Bundled-only extractors should produce spans from both sources."""
    spans = extract_trace(run_dir)
    sources = {s.source for s in spans}
    assert {"metaproc-engine", "claude-agent"}.issubset(sources)


def test_extract_then_cli_loads_same_spans(run_dir: Path):
    spans_runtime = extract_trace(run_dir)
    runner = CliRunner()
    result = runner.invoke(app, ["trace", str(run_dir), "--extract"])
    assert result.exit_code == 0, result.stderr
    assert trace_path(run_dir).is_file()
    assert trace_path(run_dir) == run_dir / ".logs" / "derived" / "trace.jsonl"
    assert not legacy_trace_out(run_dir).exists()
    spans_loaded = read_trace(run_dir)
    assert len(spans_loaded) == len(spans_runtime)


def test_read_trace_loads_compressed_trace_store(run_dir: Path):
    spans_runtime = extract_trace(run_dir)
    trace_path(run_dir).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(trace_path(run_dir).with_name("trace.jsonl.gz"), "wt", encoding="utf-8") as fh:
        for span in spans_runtime:
            fh.write(span.model_dump_json() + "\n")

    spans_loaded = read_trace(run_dir)
    assert len(spans_loaded) == len(spans_runtime)


def test_cli_errors_when_no_trace_and_no_extract(run_dir: Path):
    runner = CliRunner()
    result = runner.invoke(app, ["trace", str(run_dir)])
    assert result.exit_code == 1


def test_cli_loads_existing_trace(run_dir: Path):
    runner = CliRunner()
    extract_result = runner.invoke(app, ["trace", str(run_dir), "--extract"])
    assert extract_result.exit_code == 0
    result = runner.invoke(app, ["trace", str(run_dir)])
    assert result.exit_code == 0


def test_cli_source_tool_rollup(run_dir: Path):
    runner = CliRunner()
    extract_result = runner.invoke(app, ["trace", str(run_dir), "--extract"])
    assert extract_result.exit_code == 0
    result = runner.invoke(app, ["trace", str(run_dir), "--source-tool-rollup"])
    assert result.exit_code == 0
    assert "tool.family" in result.stdout
    assert "raw_log_ref.coverage_pct" in result.stdout
    assert "local_input" in result.stdout
