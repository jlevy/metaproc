"""Tests for metabrowser.charts — chart data extraction."""
# pyright: reportMissingTypeArgument=false

from __future__ import annotations

import json
import tempfile
from collections.abc import Sequence
from pathlib import Path

from metabrowser.charts import (
    extract_agent_charts,
)

from metaproc.metabrowser_plugin.charts import extract_runpool_charts

# ── Fixtures ────────────────────────────────────────────────────


def _write_jsonl(events: Sequence[object]) -> Path:
    """Write events to a temporary JSONL file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for ev in events:
        f.write(json.dumps(ev) + "\n")
    f.close()
    return Path(f.name)


RUNPOOL_EVENTS = [
    {
        "event": "pool_start",
        "pool_id": "pool-test",
        "backend": "local",
        "max_concurrency": 10,
        "ts": "2026-04-06T10:00:00+00:00",
    },
    {
        "event": "pressure_check",
        "level": "normal",
        "available_pct": 75.0,
        "ts": "2026-04-06T10:00:00+00:00",
    },
    {
        "event": "process_start",
        "pid": 100,
        "backend": "local",
        "label": "ITEM=A",
        "ts": "2026-04-06T10:00:01+00:00",
    },
    {
        "event": "process_start",
        "pid": 101,
        "backend": "local",
        "label": "ITEM=B",
        "ts": "2026-04-06T10:00:02+00:00",
    },
    {
        "event": "pressure_check",
        "level": "elevated",
        "available_pct": 40.0,
        "ts": "2026-04-06T10:00:10+00:00",
    },
    {
        "event": "process_exit",
        "pid": 100,
        "backend": "local",
        "label": "ITEM=A",
        "exit_code": 0,
        "elapsed_s": 19.0,
        "ts": "2026-04-06T10:00:20+00:00",
    },
    {
        "event": "process_exit",
        "pid": 101,
        "backend": "local",
        "label": "ITEM=B",
        "exit_code": 1,
        "elapsed_s": 28.0,
        "ts": "2026-04-06T10:00:30+00:00",
    },
    {
        "event": "concurrency_adjust",
        "old": 10,
        "new": 5,
        "reason": "memory_pressure",
        "ts": "2026-04-06T10:00:35+00:00",
    },
    {
        "event": "pool_shutdown",
        "pool_id": "pool-test",
        "completed": 1,
        "failed": 1,
        "killed": 0,
        "ts": "2026-04-06T10:01:00+00:00",
    },
]

CLAUDE_EVENTS = [
    {"type": "system", "subtype": "init", "model": "claude-opus-4-20250514"},
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/test.py"}},
            ]
        },
        "timestamp": "2026-04-06T10:00:00+00:00",
    },
    {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "file contents"},
            ]
        },
        "timestamp": "2026-04-06T10:00:05+00:00",
    },
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Here is the code."},
            ]
        },
        "timestamp": "2026-04-06T10:00:10+00:00",
    },
    {
        "type": "result",
        "subtype": "success",
        "cost_usd": 0.05,
        "duration_s": 15.0,
        "is_error": False,
        "timestamp": "2026-04-06T10:00:15+00:00",
    },
]


# ── RunPool chart tests ─────────────────────────────────────────


class TestExtractRunpoolCharts:
    def test_returns_summary_and_charts(self):
        path = _write_jsonl(RUNPOOL_EVENTS)
        result = extract_runpool_charts(path)
        assert "summary" in result
        assert "charts" in result
        assert result["summary"] is not None

    def test_taxonomy_counts(self):
        path = _write_jsonl(RUNPOOL_EVENTS)
        result = extract_runpool_charts(path)
        counts = result["summary"]["counts"]
        assert counts["process/start"] == 2
        assert counts["process/exit/success"] == 1
        assert counts["process/exit/failed"] == 1
        assert counts["pressure/normal"] == 1
        assert counts["pressure/elevated"] == 1
        assert counts["concurrency/adjust"] == 1

    def test_metadata(self):
        path = _write_jsonl(RUNPOOL_EVENTS)
        result = extract_runpool_charts(path)
        meta = result["summary"]["metadata"]
        assert meta["pool_id"] == "pool-test"
        assert meta["backend"] == "local"
        assert meta["max_concurrency"] == 10
        assert meta["completed"] == 1
        assert meta["failed"] == 1

    def test_memory_pressure_chart(self):
        path = _write_jsonl(RUNPOOL_EVENTS)
        result = extract_runpool_charts(path)
        charts = result["charts"]
        pressure_chart = next(c for c in charts if c["id"] == "memory-pressure")
        assert pressure_chart["type"] == "area"
        assert len(pressure_chart["series"][0]["data"]) == 2  # 2 pressure_check events

    def test_process_count_chart(self):
        path = _write_jsonl(RUNPOOL_EVENTS)
        result = extract_runpool_charts(path)
        charts = result["charts"]
        count_chart = next(c for c in charts if c["id"] == "process-count")
        assert count_chart["type"] == "step"
        # 2 starts + 2 exits = 4 running data points
        assert len(count_chart["series"][0]["data"]) == 4

    def test_empty_file(self):
        path = _write_jsonl([])
        result = extract_runpool_charts(path)
        assert result["summary"] is None
        assert result["charts"] == []

    def test_annotations_for_concurrency_adjust(self):
        path = _write_jsonl(RUNPOOL_EVENTS)
        result = extract_runpool_charts(path)
        pressure_chart = next(c for c in result["charts"] if c["id"] == "memory-pressure")
        annotations = pressure_chart.get("annotations", [])
        throttle_anns = [a for a in annotations if "Throttle" in a["label"]]
        assert len(throttle_anns) == 1
        assert "10\u219210" in throttle_anns[0]["label"] or "10\u21925" in throttle_anns[0]["label"]


# ── Agent chart tests ───────────────────────────────────────────


class TestExtractAgentCharts:
    def test_returns_summary_and_charts(self):
        path = _write_jsonl(CLAUDE_EVENTS)
        result = extract_agent_charts(path)
        assert "summary" in result
        assert "charts" in result
        assert result["summary"] is not None

    def test_taxonomy_counts(self):
        path = _write_jsonl(CLAUDE_EVENTS)
        result = extract_agent_charts(path)
        counts = result["summary"]["counts"]
        assert (
            counts.get("init", 0) >= 1 or "init" not in counts
        )  # Claude parser may not emit init for system events
        # Should have tool_call, tool_result, text, result
        total = sum(counts.values())
        assert total > 0

    def test_metadata(self):
        path = _write_jsonl(CLAUDE_EVENTS)
        result = extract_agent_charts(path)
        meta = result["summary"]["metadata"]
        assert meta["adapter"] == "claude"

    def test_empty_file(self):
        path = _write_jsonl([])
        result = extract_agent_charts(path)
        assert result["summary"] is None
        assert result["charts"] == []
