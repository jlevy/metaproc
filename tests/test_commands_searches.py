"""Tests for ``metaproc searches list``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.trace.schema import SpanKind, TraceEvent
from metaproc.trace.store import write_trace


def _make_span(
    *,
    adapter: str,
    step: str,
    item: str,
    tool_name: str,
    tool_family: str,
    kind: SpanKind = "tool_call",
    source: str = "claude-agent",
    provider: str | None = None,
    tool_operation: str | None = None,
    ticker: str | None = None,
    query: str | None = None,
    url: str | None = None,
    trace_id: str = "run-002",
    span_id: str | None = None,
) -> TraceEvent:
    attrs: dict[str, object] = {
        "adapter.type": adapter,
        "step.id": step,
        "item.key": item,
        "tool.name": tool_name,
        "tool.family": tool_family,
    }
    if provider is not None:
        attrs["provider"] = provider
    if tool_operation is not None:
        attrs["tool.operation"] = tool_operation
    if ticker is not None:
        attrs["item.ticker"] = ticker
    if query is not None:
        attrs["tool.input.query"] = query
    if url is not None:
        attrs["tool.input.url"] = url
    return TraceEvent(
        trace_id=trace_id,
        span_id=span_id or f"{adapter}-{step}-{item}-{tool_name}-{tool_family}",
        name=tool_name,
        kind=kind,
        source=source,
        ts_start="2026-05-20T10:00:00Z",
        ts_end="2026-05-20T10:00:01Z",
        duration_ms=800,
        status="ok",
        attributes=attrs,
    )


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run-002"
    d.mkdir()
    spans = [
        _make_span(
            adapter="claude",
            step="research",
            item="AAPL",
            tool_name="google_web_search",
            tool_family="web",
            query="AAPL earnings Q2 2026",
        ),
        _make_span(
            adapter="claude",
            step="research",
            item="AAPL",
            tool_name="filtered_web_search",
            tool_family="web",
            query="AAPL revenue forecast",
            url="https://example.com",
        ),
        _make_span(
            adapter="gemini",
            step="research",
            item="GOOG",
            tool_name="google_web_search",
            tool_family="web",
            query="GOOG cloud revenue",
        ),
        _make_span(
            adapter="",
            step="analysis-research",
            item="CXM",
            ticker="CXM",
            tool_name="company_web_research_bundle",
            tool_family="web",
            tool_operation="search",
            provider="exa",
            query="Sprinklr CXM Q2 2026 EPS revenue consensus",
            kind="provider_call",
            source="web-bundle",
            span_id="web-bundle-CXM-exa-q001",
        ),
        _make_span(
            adapter="",
            step="analysis-research",
            item="CXM",
            ticker="CXM",
            tool_name="company_web_research_bundle",
            tool_family="web",
            tool_operation="fetch",
            provider="exa",
            url="https://example.com/cxm",
            kind="provider_call",
            source="web-bundle",
            span_id="web-bundle-CXM-exa-fetch",
        ),
        # Non-web tool call -- should NOT appear in searches output
        _make_span(
            adapter="claude",
            step="research",
            item="AAPL",
            tool_name="Read",
            tool_family="file",
        ),
    ]
    write_trace(d, spans)
    return d


runner = CliRunner()


class TestSearchesList:
    def test_default_columns(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["searches", "list", str(run_dir)])
        assert result.exit_code == 0, result.output
        assert "adapter.type" in result.output
        assert "tool.input.query" in result.output
        lines = result.output.strip().split("\n")
        # header + 3 agent web-search rows + 1 web-bundle search row
        # (the file-family Read and web-bundle fetch row are excluded)
        assert len(lines) == 5

    def test_excludes_non_web(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["searches", "list", str(run_dir)])
        assert result.exit_code == 0
        # "Read" tool (file family) should not appear
        data_lines = result.output.strip().split("\n")[1:]
        for line in data_lines:
            assert "file" not in line

    def test_filter_adapter(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["searches", "list", str(run_dir), "--adapter", "gemini"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 2  # header + 1 gemini web call

    def test_filter_engine(self, run_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["searches", "list", str(run_dir), "--engine", "filtered_web_search"],
        )
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 2  # header + 1

    def test_filter_step(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["searches", "list", str(run_dir), "--step", "research"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 4  # header + 3

    def test_includes_web_bundle_search_provider_calls(self, run_dir: Path) -> None:
        result = runner.invoke(
            app, ["searches", "list", str(run_dir), "--step", "analysis-research"]
        )
        assert result.exit_code == 0
        assert "provider_call" in result.output
        assert "web-bundle" in result.output
        assert "Sprinklr CXM Q2 2026" in result.output
        assert "https://example.com/cxm" not in result.output

    def test_custom_project(self, run_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["searches", "list", str(run_dir), "--project", "tool.name,tool.input.query"],
        )
        assert result.exit_code == 0
        assert "tool.name" in result.output
        assert "tool.input.query" in result.output
        assert "adapter.type" not in result.output

    def test_json_output(self, run_dir: Path) -> None:
        result = runner.invoke(app, ["searches", "list", str(run_dir), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 4
        assert "tool.input.query" in data[0]

    def test_no_trace_errors_cleanly(self, tmp_path: Path) -> None:
        d = tmp_path / "empty-run"
        d.mkdir()
        result = runner.invoke(app, ["searches", "list", str(d)])
        assert result.exit_code == 1

    def test_missing_run_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["searches", "list", str(tmp_path / "nonexistent")])
        assert result.exit_code != 0
