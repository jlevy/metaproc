"""Code-mode `run-parallel` resource attribution tests."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.logutil.resource_events import read_events
from metaproc.models.resources import SampleEvent
from metaproc.paths import LOGS_DIR


def test_code_mode_persists_item_resource_samples(tmp_path: Path) -> None:
    source_path = tmp_path / "items.md"
    source_path.write_text(
        "---\nprogress:\n  process: parallel-code\n  items:\n"
        "    - ticker: AAPL\n      market: NASDAQ\n---\n"
    )
    process_path = tmp_path / "parallel-code.process.md"
    process_path.write_text(
        f"""---
process:
  name: parallel-code
  steps:
    - id: verify-item
      mode: code
      command: /bin/sh -c 'test "{{{{ticker}}}}-{{{{market}}}}" = AAPL-NASDAQ && sleep 0.2'
      inputs:
        tickers:
          path: "{source_path}"
          kind: file
          format: frontmatter-md
      for_each:
        over: tickers
        bind: ticker
        bind_fields: [ticker, market]
        key: "{{{{ticker}}}}-{{{{market}}}}"
---
# Parallel code
"""
    )

    result = CliRunner().invoke(
        app,
        [
            "run-parallel",
            str(process_path),
            "--step",
            "verify-item",
            "--items",
            "AAPL",
            "--var",
            "RUN_ID=test-run",
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
        ],
    )

    assert result.exit_code == 0, result.output
    event_path = tmp_path / "runs" / "test-run" / LOGS_DIR / "resource-events.jsonl"
    samples = [event for event in read_events(event_path) if isinstance(event, SampleEvent)]
    assert samples
    assert {(event.hierarchy.step_node_id, event.hierarchy.item_key) for event in samples} == {
        ("verify-item", "AAPL-NASDAQ")
    }
