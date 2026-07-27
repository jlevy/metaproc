"""P4 — cross-run-dir trace comparison matrix.

Pins the contract that `cross_run_matrix()` (and `metaproc compare-trace`)
loads N trace stores and produces a side-by-side matrix using the same
aggregator primitive as `metaproc trace --rollup`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaproc.commands.compare_trace import cross_run_matrix


def _write_trace(run_dir: Path, spans: list[dict[str, object]]) -> None:
    p = run_dir / ".logs" / "derived" / "trace.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as fh:
        for s in spans:
            fh.write(json.dumps(s) + "\n")


def _span(
    *,
    span_id: str,
    kind: str = "tool_call",
    source: str = "claude-agent",
    tool_name: str | None = None,
    tool_family: str | None = None,
    adapter_type: str | None = None,
    step_id: str | None = None,
    cost_usd: float | None = None,
) -> dict[str, object]:
    attrs: dict[str, object] = {}
    if tool_name:
        attrs["tool.name"] = tool_name
    if tool_family:
        attrs["tool.family"] = tool_family
    if adapter_type:
        attrs["adapter.type"] = adapter_type
    if step_id:
        attrs["step.id"] = step_id
    if cost_usd is not None:
        attrs["attempt.cost_usd"] = cost_usd
    return {
        "trace_id": "t",
        "span_id": span_id,
        "name": tool_name or "x",
        "kind": kind,
        "source": source,
        "ts_start": "2026-05-26T00:00:00Z",
        "status": "ok",
        "attributes": attrs,
    }


@pytest.fixture
def two_run_dirs(tmp_path: Path) -> list[Path]:
    """Two synthetic run-dirs with trace stores."""
    r1 = tmp_path / "claude-run"
    r2 = tmp_path / "codex-run"
    _write_trace(
        r1,
        [
            _span(
                span_id="a",
                tool_name="WebSearch",
                tool_family="web",
                adapter_type="claude-code-cli",
                step_id="research-step",
            ),
            _span(
                span_id="b",
                tool_name="WebSearch",
                tool_family="web",
                adapter_type="claude-code-cli",
                step_id="research-step",
            ),
            _span(
                span_id="c",
                tool_name="Bash",
                tool_family="shell",
                adapter_type="claude-code-cli",
                step_id="research-step",
            ),
        ],
    )
    _write_trace(
        r2,
        [
            _span(
                span_id="d",
                tool_name="bash",
                tool_family="shell",
                adapter_type="codex-cli",
                step_id="research-step",
                source="codex-agent",
            ),
            _span(
                span_id="e",
                tool_name="bash",
                tool_family="shell",
                adapter_type="codex-cli",
                step_id="research-step",
                source="codex-agent",
            ),
        ],
    )
    return [r1, r2]


def test_cross_run_matrix_counts_tool_calls_per_run(two_run_dirs: list[Path]) -> None:
    matrix = cross_run_matrix(
        two_run_dirs,
        group_keys=["tool.family"],
        metrics=["count"],
        kind_filter="tool_call",
    )
    by_family = {row["tool.family"]: row for row in matrix}
    # web: 2 from claude-run, 0 from codex-run
    web = by_family["web"]
    assert web["claude-run"] == 2
    assert web["codex-run"] is None
    # shell: 1 from claude-run, 2 from codex-run
    shell = by_family["shell"]
    assert shell["claude-run"] == 1
    assert shell["codex-run"] == 2


def test_cross_run_matrix_with_multi_groupkey(two_run_dirs: list[Path]) -> None:
    """Group on (adapter.type, step.id, tool.family) → rich cross-adapter matrix."""
    matrix = cross_run_matrix(
        two_run_dirs,
        group_keys=["adapter.type", "step.id", "tool.family"],
        metrics=["count"],
        kind_filter="tool_call",
    )
    # Each group only appears in one run (because adapter.type differs);
    # the matrix should have 3 rows (web@claude, shell@claude, shell@codex)
    assert len(matrix) == 3


def test_cross_run_matrix_cost_metric(tmp_path: Path) -> None:
    """sum:attempt.cost_usd on attempt spans roll up across runs."""
    r1 = tmp_path / "lane-a"
    r2 = tmp_path / "lane-b"
    _write_trace(
        r1,
        [
            _span(
                span_id="a",
                kind="attempt",
                adapter_type="claude-code-cli",
                step_id="s1",
                cost_usd=0.10,
            ),
            _span(
                span_id="b",
                kind="attempt",
                adapter_type="claude-code-cli",
                step_id="s1",
                cost_usd=0.05,
            ),
        ],
    )
    _write_trace(
        r2,
        [
            _span(
                span_id="c",
                kind="attempt",
                adapter_type="codex-cli",
                step_id="s1",
                cost_usd=1.20,
            ),
        ],
    )
    matrix = cross_run_matrix(
        [r1, r2],
        group_keys=["adapter.type"],
        metrics=["sum:attempt.cost_usd"],
        kind_filter="attempt",
    )
    by_adapter = {row["adapter.type"]: row for row in matrix}
    assert by_adapter["claude-code-cli"]["lane-a"] == 0.15
    assert by_adapter["claude-code-cli"]["lane-b"] is None
    assert by_adapter["codex-cli"]["lane-b"] == 1.2


def test_cross_run_matrix_missing_trace_raises(tmp_path: Path) -> None:
    r1 = tmp_path / "lane-a"
    r1.mkdir()
    # no trace.jsonl
    with pytest.raises(FileNotFoundError, match="no trace store"):
        cross_run_matrix(
            [r1],
            group_keys=["tool.family"],
            metrics=["count"],
        )


def test_cross_run_matrix_handles_empty_trace(tmp_path: Path) -> None:
    """Empty trace.jsonl is valid (zero rows), not an error."""
    r1 = tmp_path / "empty-run"
    p = r1 / ".logs" / "derived" / "trace.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("")
    r2 = tmp_path / "nonempty"
    _write_trace(
        r2,
        [
            _span(
                span_id="a",
                tool_name="x",
                tool_family="web",
                adapter_type="claude-code-cli",
            ),
        ],
    )
    matrix = cross_run_matrix(
        [r1, r2],
        group_keys=["tool.family"],
        metrics=["count"],
        kind_filter="tool_call",
    )
    # Only 'web' from the non-empty run.
    web_row = next(row for row in matrix if row["tool.family"] == "web")
    assert web_row["empty-run"] is None
    assert web_row["nonempty"] == 1
