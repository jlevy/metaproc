"""Code handlers can record the model calls they make, and those calls reach the run's
resource ledger and usage summary under the step and item that made them.

Agent steps are measured from their adapter logs. A handler that calls a model API
itself writes no log metaproc parses, so its tokens and cost were invisible until it
reported them through ``StepContext.record_usage``. A tool that writes typed events to
``.logs/tools/<tool>/resource-events.jsonl`` was likewise misread as an agent session
and came out unmeasured.
"""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.engine.resource_finalization import finalize_run_resources
from metaproc.engine.resource_rollup import _read_tool_events
from metaproc.engine.resource_sampling import CODE_HANDLER_SOURCE_KIND, record_code_usage
from metaproc.engine.resource_summary import RESOURCE_USAGE_SUMMARY_FILE
from metaproc.io import fmf_read_frontmatter
from metaproc.logutil.resource_events import ResourceEventLogger, read_events
from metaproc.models.authored import StepContext
from metaproc.models.resource_budget import FinalizationState
from metaproc.models.resources import HierarchyRef, Metrics, ProviderRef, SourceRef, UsageEvent
from metaproc.paths import LOGS_DIR, RESOURCE_EVENTS_FILE, tool_resource_events_log


def _usage_events(run_dir: Path) -> list[UsageEvent]:
    events = read_events(run_dir / LOGS_DIR / RESOURCE_EVENTS_FILE)
    return [e for e in events if isinstance(e, UsageEvent)]


def _summary_totals(run_dir: Path) -> dict[str, object]:
    frontmatter = fmf_read_frontmatter(run_dir / RESOURCE_USAGE_SUMMARY_FILE)
    assert isinstance(frontmatter, dict)
    return frontmatter["resource_usage"]["totals"]


def test_record_code_usage_writes_a_priced_usage_event(tmp_path: Path) -> None:
    record_code_usage(
        run_dir=tmp_path,
        run_id="proc/run-1",
        step_node_id="transcribe",
        item_key="abc",
        provider="google",
        product="vertex-ai",
        model="gemini-3.5-flash-lite",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    (event,) = _usage_events(tmp_path)
    assert event.hierarchy.run_id == "proc/run-1"
    assert event.hierarchy.step_node_id == "transcribe"
    assert event.hierarchy.item_key == "abc"
    assert event.source.kind == CODE_HANDLER_SOURCE_KIND
    assert event.metrics.input_tokens == 1_000_000
    assert event.taxonomy.model_path == ["model", "google", "gemini-3.5-flash-lite"]
    # The step's own samples measure wall time; the call must not add it again.
    assert event.metrics.wall_time_s is None
    # Priced from the bundled pricing table rather than left unmeasured.
    assert event.metrics.list_cost_usd is not None
    assert event.metrics.list_cost_usd > 0


def test_explicit_cost_and_cache_hit_are_kept(tmp_path: Path) -> None:
    record_code_usage(
        run_dir=tmp_path,
        run_id="proc/run-1",
        step_node_id="verify",
        provider="google",
        product="vertex-ai",
        model="unpriced-model",
        input_tokens=10,
        output_tokens=5,
        list_cost_usd=0.5,
        cache_hit=True,
    )
    (event,) = _usage_events(tmp_path)
    assert event.metrics.list_cost_usd == 0.5
    assert event.metrics.cache_hits == 1
    assert event.metrics.cache_misses == 0


def test_step_context_forwards_usage_and_is_inert_outside_a_run() -> None:
    seen: list[dict[str, object]] = []
    ctx = StepContext(
        {"k": "v"}, attempt=2, item_key="i1", usage_recorder=lambda **kw: seen.append(kw)
    )
    ctx.record_usage(
        provider="google", product="vertex-ai", model="m", input_tokens=1, output_tokens=2
    )
    (call,) = seen
    assert {key: call[key] for key in ("provider", "model", "input_tokens", "output_tokens")} == {
        "provider": "google",
        "model": "m",
        "input_tokens": 1,
        "output_tokens": 2,
    }
    assert (ctx.attempt, ctx.item_key, ctx["k"]) == (2, "i1", "v")
    StepContext({}).record_usage(
        provider="p", product="q", model="m", input_tokens=0, output_tokens=0
    )


_HANDLERS = '''\
"""A handler that reports one model call per item."""

from __future__ import annotations

from pathlib import Path


def call(ctx, step) -> None:  # noqa: ARG001
    ctx.record_usage(
        provider="google",
        product="vertex-ai",
        model="gemini-3.5-flash-lite",
        input_tokens=1000,
        output_tokens=100,
    )
    out = Path(ctx["RUNS_DIR"]) / ctx["RUN_ID"] / "out" / f"{ctx.item_key}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"attempt={ctx.attempt}\\n")
'''

_ROSTER = """\
---
progress:
  schema: metaproc:ProgressSpec/0.1
  process: usage
  items:
    - item: alfa
    - item: brvo
---
# Roster
"""

_PROCESS = """\
---
process:
  name: usage
  deps:
    roster: { path: ./roster.md, as: path }
  steps:
    - id: call
      mode: code
      handler: "handlers.py:call"
      for_each:
        over: deps.roster
        bind: item
        bind_fields: [item]
        key: "{{item}}"
      outputs:
        out: { path: "{{run.dir}}/out/{{item}}.txt", kind: file }
---
# Usage
"""


def test_handler_usage_reaches_the_run_summary(tmp_path: Path) -> None:
    process_dir = tmp_path / "process"
    process_dir.mkdir()
    (process_dir / "handlers.py").write_text(_HANDLERS)
    (process_dir / "roster.md").write_text(_ROSTER)
    process_path = process_dir / "usage.process.md"
    process_path.write_text(textwrap.dedent(_PROCESS))
    runs_dir = tmp_path / "runs"
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            "RUN_ID=r1",
        ],
    )
    assert result.exit_code == 0, result.output
    run_dir = runs_dir / "r1"

    events = [e for e in _usage_events(run_dir) if e.source.kind == CODE_HANDLER_SOURCE_KIND]
    assert sorted(e.hierarchy.item_key or "" for e in events) == ["alfa", "brvo"]
    assert {e.hierarchy.step_node_id for e in events} == {"call"}
    assert (run_dir / "out" / "alfa.txt").read_text() == "attempt=1\n"

    totals = _summary_totals(run_dir)
    assert totals["input_tokens"] == 2000
    assert totals["output_tokens"] == 200
    assert isinstance(totals["list_cost_usd"], float) and totals["list_cost_usd"] > 0


def test_tool_resource_events_are_read_as_typed_events(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    stream = tool_resource_events_log(run_dir, "gemini")
    stream.parent.mkdir(parents=True)
    event = UsageEvent(
        ts=datetime.now(UTC),
        hierarchy=HierarchyRef(run_id="proc/r1", step_node_id="call", item_key="a"),
        provider=ProviderRef(provider="google", product="vertex-ai", model="m"),
        source=SourceRef(kind="gemini_api", path=".logs/tools/gemini/resource-events.jsonl"),
        metrics=Metrics(input_tokens=1000, output_tokens=100, list_cost_usd=0.01),
    )
    with ResourceEventLogger(stream) as logger:
        logger.write(event)
    with stream.open("a") as fh:
        fh.write("{not an event}\n")

    built = finalize_run_resources(
        run_dir, outcome=FinalizationState.COMPLETED, legacy_run_id="proc/r1"
    )

    (source_log,) = built.document.source_logs
    assert source_log.kind == "tool_events"
    assert source_log.adapter is None
    totals = _summary_totals(run_dir)
    assert totals["input_tokens"] == 1000
    assert totals["output_tokens"] == 100
    assert totals["list_cost_usd"] == 0.01


def test_malformed_tool_event_warning_omits_rejected_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stream = tmp_path / "resource-events.jsonl"
    event = UsageEvent(
        ts=datetime.now(UTC),
        hierarchy=HierarchyRef(run_id="proc/r1"),
        provider=ProviderRef(provider="google", product="vertex-ai", model="m"),
        source=SourceRef(kind="gemini_api", path="resource-events.jsonl"),
        metrics=Metrics(input_tokens=10),
    )
    rejected = event.model_dump(mode="json")
    rejected["metrics"]["input_tokens"] = "Bearer synthetic-resource-event-secret"
    stream.write_text(json.dumps(rejected) + "\n" + event.model_dump_json() + "\n")

    assert _read_tool_events(stream) == [event]
    assert f"{stream}:1" in caplog.text
    assert "skipping malformed resource event" in caplog.text
    assert "synthetic-resource-event-secret" not in caplog.text
    assert "input_value" not in caplog.text
