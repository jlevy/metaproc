"""Unit tests for the metaproc engine + runpool extractor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from metaproc.paths import legacy_runpool_step_events, runpool_step_events
from metaproc.trace.extractors.metaproc_engine import MetaprocEngineExtractor


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run-x"
    d.mkdir()
    return d


def _write_jsonl(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload))


def test_detect_false_when_no_engine_logs(run_dir: Path):
    assert MetaprocEngineExtractor().detect(run_dir) is False


def test_detect_true_for_process_events(run_dir: Path):
    _write_jsonl(run_dir / ".logs" / "process-events.jsonl", [{"event": "process_start"}])
    assert MetaprocEngineExtractor().detect(run_dir) is True


def test_extract_run_level_step_from_process_events(run_dir: Path):
    _write_jsonl(
        run_dir / ".logs" / "process-events.jsonl",
        [
            {
                "ts": "2026-05-12T00:00:00Z",
                "event": "process_start",
                "process": "analysis-research",
                "run_id": "run-x",
                "backend": "local",
                "step_count": 6,
            },
            {
                "ts": "2026-05-12T00:00:01Z",
                "event": "level_start",
                "level": 0,
                "steps": ["scaffold-roster"],
            },
            {
                "ts": "2026-05-12T00:00:02Z",
                "event": "step_start",
                "step_id": "scaffold-roster",
                "mode": "code",
            },
            {
                "ts": "2026-05-12T00:00:05Z",
                "event": "step_complete",
                "step_id": "scaffold-roster",
                "elapsed_s": 3.0,
            },
            {"ts": "2026-05-12T00:00:05Z", "event": "level_complete", "level": 0, "elapsed_s": 3.5},
        ],
    )
    spans = list(MetaprocEngineExtractor().extract(run_dir, trace_id="run-x"))
    kinds = {s.kind: s for s in spans}
    assert "run" in kinds
    assert "level" in kinds
    assert "step" in kinds
    assert kinds["step"].status == "ok"
    assert kinds["step"].duration_ms == pytest.approx(3000.0)
    assert kinds["level"].duration_ms == pytest.approx(3500.0)


def test_extract_step_fail_classified_as_error(run_dir: Path):
    _write_jsonl(
        run_dir / ".logs" / "process-events.jsonl",
        [
            {
                "ts": "2026-05-12T00:00:00Z",
                "event": "process_start",
                "process": "analysis-research",
            },
            {
                "ts": "2026-05-12T00:00:01Z",
                "event": "step_start",
                "step_id": "research-step",
                "mode": "agent",
            },
            {
                "ts": "2026-05-12T00:00:10Z",
                "event": "step_fail",
                "step_id": "research-step",
                "elapsed_s": 9.0,
                "error": "boom",
            },
        ],
    )
    spans = list(MetaprocEngineExtractor().extract(run_dir, trace_id="run-x"))
    step = next(s for s in spans if s.kind == "step")
    assert step.status == "error"
    assert step.error is not None
    assert step.error["message"] == "boom"


def test_extract_subprocess_from_runpool_events(run_dir: Path):
    _write_jsonl(
        runpool_step_events(run_dir, "research-step"),
        [
            {"event": "pool_start", "ts": "2026-05-12T00:00:00Z", "max_concurrency": 4},
            {
                "event": "process_start",
                "ts": "2026-05-12T00:00:01Z",
                "pid": 1234,
                "backend": "local",
                "label": "ticker=MNDY",
            },
            {
                "event": "process_exit",
                "ts": "2026-05-12T00:01:00Z",
                "pid": 1234,
                "exit_code": 0,
                "elapsed_s": 59.0,
                "peak_rss_bytes": 100000000,
            },
        ],
    )
    spans = list(MetaprocEngineExtractor().extract(run_dir, trace_id="run-x"))
    subprocesses = [s for s in spans if s.kind == "subprocess"]
    assert len(subprocesses) == 1
    sp = subprocesses[0]
    assert sp.attributes["subprocess.pid"] == 1234
    assert sp.attributes["subprocess.label"] == "ticker=MNDY"
    assert sp.status == "ok"
    assert sp.duration_ms == pytest.approx(59000.0)


def test_extract_subprocess_nonzero_exit_is_error(run_dir: Path):
    _write_jsonl(
        runpool_step_events(run_dir, "research-step"),
        [
            {
                "event": "process_start",
                "ts": "2026-05-12T00:00:01Z",
                "pid": 5,
                "backend": "local",
                "label": "ticker=BOOM",
            },
            {
                "event": "process_exit",
                "ts": "2026-05-12T00:00:05Z",
                "pid": 5,
                "exit_code": 1,
                "elapsed_s": 4.0,
            },
        ],
    )
    spans = list(MetaprocEngineExtractor().extract(run_dir, trace_id="run-x"))
    subprocesses = [s for s in spans if s.kind == "subprocess"]
    assert len(subprocesses) == 1
    assert subprocesses[0].status == "error"


def test_extract_item_from_result_yaml(run_dir: Path):
    _write_yaml(
        run_dir / ".state" / "tasks" / "research-step" / "MNDY" / "result.yaml",
        {
            "run_id": "run-x",
            "step_id": "research-step",
            "state": "completed",
            "validated": True,
            "published_at": "2026-05-12T00:05:00",
        },
    )
    _write_yaml(
        run_dir / ".state" / "tasks" / "research-step" / "FAIL" / "result.yaml",
        {
            "run_id": "run-x",
            "step_id": "research-step",
            "state": "failed",
            "validated": False,
            "published_at": "2026-05-12T00:05:00",
        },
    )
    spans = list(MetaprocEngineExtractor().extract(run_dir, trace_id="run-x"))
    items = [s for s in spans if s.kind == "item"]
    assert len(items) == 2
    by_ticker = {s.attributes["item.key"]: s for s in items}
    assert by_ticker["MNDY"].status == "ok"
    assert by_ticker["FAIL"].status == "error"


def test_extract_pressure_check_is_internal_span(run_dir: Path):
    _write_jsonl(
        runpool_step_events(run_dir, "research-step"),
        [
            {
                "event": "pressure_check",
                "ts": "2026-05-12T00:00:01Z",
                "level": "elevated",
                "available_pct": 44.0,
            }
        ],
    )
    spans = list(MetaprocEngineExtractor().extract(run_dir, trace_id="run-x"))
    internal = [s for s in spans if s.kind == "internal"]
    assert len(internal) == 1
    assert internal[0].status == "ok"
    assert internal[0].attributes["internal.kind"] == "pressure_check"
    assert internal[0].attributes["internal.available_pct"] == 44.0


def test_extract_legacy_runpool_events_for_unmarked_runs(run_dir: Path):
    _write_jsonl(
        legacy_runpool_step_events(run_dir, "research-step"),
        [
            {
                "event": "process_start",
                "ts": "2026-05-12T00:00:01Z",
                "pid": 7,
                "backend": "local",
                "label": "ticker=OLD",
            },
            {
                "event": "process_exit",
                "ts": "2026-05-12T00:00:05Z",
                "pid": 7,
                "exit_code": 0,
                "elapsed_s": 4.0,
            },
        ],
    )

    spans = list(MetaprocEngineExtractor().extract(run_dir, trace_id="run-x"))
    subprocesses = [s for s in spans if s.kind == "subprocess"]
    assert len(subprocesses) == 1
    assert subprocesses[0].attributes["step.id"] == "research-step"
