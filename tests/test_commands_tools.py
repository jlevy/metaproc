"""Tests for ``metaproc tools list`` and ``metaproc tools rollup``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.trace.schema import SpanStatus, TraceEvent
from metaproc.trace.store import write_trace


def _make_tool_span(
    *,
    adapter: str,
    step: str,
    item: str,
    tool_name: str,
    tool_family: str,
    status: SpanStatus = "ok",
    trace_id: str = "run-001",
    span_id: str | None = None,
) -> TraceEvent:
    return TraceEvent(
        trace_id=trace_id,
        span_id=span_id or f"{adapter}-{step}-{item}-{tool_name}",
        name=tool_name,
        kind="tool_call",
        source="claude-agent",
        ts_start="2026-05-20T10:00:00Z",
        ts_end="2026-05-20T10:00:01Z",
        duration_ms=1000,
        status=status,
        attributes={
            "adapter.type": adapter,
            "step.id": step,
            "item.key": item,
            "tool.name": tool_name,
            "tool.family": tool_family,
        },
    )


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run-001"
    d.mkdir()
    spans = [
        _make_tool_span(
            adapter="claude",
            step="research",
            item="AAPL",
            tool_name="Read",
            tool_family="file",
        ),
        _make_tool_span(
            adapter="claude",
            step="research",
            item="AAPL",
            tool_name="google_web_search",
            tool_family="web",
        ),
        _make_tool_span(
            adapter="gemini",
            step="research",
            item="GOOG",
            tool_name="google_web_search",
            tool_family="web",
        ),
        _make_tool_span(
            adapter="gemini",
            step="synthesis",
            item="GOOG",
            tool_name="Read",
            tool_family="file",
            status="error",
        ),
    ]
    write_trace(d, spans)
    return d


runner = CliRunner()


class TestToolsList:
    def test_default_columns(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["tools", "list", str(run_dir)])
        assert result.exit_code == 0, result.output
        assert "adapter.type" in result.output
        assert "tool.family" in result.output
        lines = result.output.strip().split("\n")
        # header + 4 data rows
        assert len(lines) == 5

    def test_filter_adapter(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["tools", "list", str(run_dir), "--adapter", "gemini"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        # header + 2 gemini rows
        assert len(lines) == 3
        for line in lines[1:]:
            assert "gemini" in line

    def test_filter_tool_family(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["tools", "list", str(run_dir), "--tool-family", "web"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 3  # header + 2 web calls
        for line in lines[1:]:
            assert "web" in line

    def test_filter_tool_name(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["tools", "list", str(run_dir), "--tool", "Read"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 3  # header + 2 Read calls

    def test_filter_step(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["tools", "list", str(run_dir), "--step", "synthesis"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 2  # header + 1

    def test_custom_project(self, run_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["tools", "list", str(run_dir), "--project", "tool.name,status"],
        )
        assert result.exit_code == 0
        assert "tool.name" in result.output
        assert "status" in result.output
        # default columns not present
        assert "adapter.type" not in result.output

    def test_json_output(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["tools", "list", str(run_dir), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 4
        assert "adapter.type" in data[0]

    def test_no_trace_errors_cleanly(self, tmp_path: Path) -> None:
        d = tmp_path / "empty-run"
        d.mkdir()
        result = runner.invoke(app, ["tools", "list", str(d)])
        assert result.exit_code == 1

    def test_missing_run_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["tools", "list", str(tmp_path / "nonexistent")])
        assert result.exit_code != 0


class TestToolsRollup:
    def test_default_rollup(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["tools", "rollup", str(run_dir)])
        assert result.exit_code == 0, result.output
        assert "adapter.type" in result.output
        assert "count" in result.output
        lines = result.output.strip().split("\n")
        # header + grouped rows (4 unique adapter+step+tool combos)
        assert len(lines) >= 2

    def test_rollup_custom_group(self, run_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["tools", "rollup", str(run_dir), "--group-by", "tool.family"],
        )
        assert result.exit_code == 0
        assert "tool.family" in result.output
        lines = result.output.strip().split("\n")
        # header + 2 families (file, web)
        assert len(lines) == 3

    def test_rollup_custom_metric(self, run_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["tools", "rollup", str(run_dir), "--metric", "count,duration_ms"],
        )
        assert result.exit_code == 0
        assert "duration_ms" in result.output

    def test_rollup_filter_adapter(self, run_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["tools", "rollup", str(run_dir), "--adapter", "claude"],
        )
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        for line in lines[1:]:
            assert "gemini" not in line

    def test_rollup_json(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["tools", "rollup", str(run_dir), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert all("count" in row for row in data)

    def test_rollup_no_trace(self, tmp_path: Path) -> None:
        d = tmp_path / "empty-run"
        d.mkdir()
        result = runner.invoke(app, ["tools", "rollup", str(d)])
        assert result.exit_code == 1
