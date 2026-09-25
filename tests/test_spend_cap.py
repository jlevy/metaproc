"""Run spend cap, its pre-dispatch check, the in-run stop, and the dispatch breaker.

Every run here is provider-free: a mapped child's code step writes an agent-format log
that reports a list cost, which is what the spend ledger reads from a real agent.
"""

from __future__ import annotations

import gzip
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner, Result

from metaproc.cli import app
from metaproc.commands.run_process import _composite_item_attempts
from metaproc.commands.status import _format_text
from metaproc.engine.dep_state import fingerprint_step
from metaproc.engine.run_status import scan_run_status
from metaproc.engine.spend_cap import (
    MappedDispatch,
    SpendLedger,
    read_spend_ledger,
)
from metaproc.errors import ValidationError
from metaproc.io import read_yaml_file
from metaproc.models.authored import DispatchBreaker, ProcessStep, StepSpend
from metaproc.models.plan import FanOut, ResolvedStep
from metaproc.paths import STATE_DIR

CHILD = """\
---
process:
  name: priced-child
  steps:
    - id: work
      mode: code
      outputs:
        report: {{ path: "{{{{run.dir}}}}/report.txt", kind: file }}
      command: >-
        /bin/sh -c 'mkdir -p "{{{{run.dir}}}}/.logs/tasks/work";
        printf "%s\\n"
        "{{\\"type\\":\\"system\\",\\"subtype\\":\\"init\\",\\"model\\":\\"claude-sonnet-4-5\\"}}"
        "{{\\"type\\":\\"result\\",\\"subtype\\":\\"success\\",\\"is_error\\":false,\\"total_cost_usd\\":{cost}}}"
        > "{{{{run.dir}}}}/.logs/tasks/work/work_{{{{TICKER}}}}_2026-01-01T00-00-00.jsonl";
        if [ "{{{{SHOULD_FAIL}}}}" = "true" ] && [ -f "{{{{FAIL_MARKER}}}}" ]; then exit 7; fi;
        printf "%s\\n" "{{{{TICKER}}}}" > "{{{{run.dir}}}}/report.txt"'
---
One item: record a list cost, then fail or write a report.
"""

PARENT = """\
---
process:
  name: priced-parent
{spend_cap}  inputs:
    fail_marker: {{ param: FAIL_MARKER, as: string, required: false, default: /nonexistent }}
  deps:
    roster:
      path: "{{{{run.dir}}}}/roster.md"
      as: path
      produced_by: write-roster.roster
    child:
      path: ./child.process.md
      as: path
  steps:
    - id: write-roster
      mode: code
      outputs:
        roster: {{ path: "{{{{run.dir}}}}/roster.md", kind: file, format: frontmatter-md }}
      command: >-
        /bin/sh -c 'mkdir -p "{{{{run.dir}}}}";
        printf "%s\\n" "---" "progress:" "  schema: metaproc:ProgressSpec/0.1"
        "  process: priced-parent" "  items:" {items} "---" "Roster." > "{{{{run.dir}}}}/roster.md"'
    - id: items
      mode: composite
      uses: deps.child
      needs: [write-roster]
{spend}      for_each:
        over: deps.roster
        bind: ticker
        bind_fields: [ticker, should_fail]
        key: "{{{{ticker}}}}"
        max_concurrency: 1
{breaker}      with:
        TICKER: "{{{{ticker}}}}"
        SHOULD_FAIL: "{{{{should_fail}}}}"
        FAIL_MARKER: "{{{{FAIL_MARKER}}}}"
---
A priced mapped composite over a roster.
"""


def _write_processes(
    process_dir: Path,
    *,
    tickers: list[tuple[str, bool]],
    cost_usd: float,
    per_item_usd: float | None = None,
    max_attempts: int = 2,
    breaker: tuple[float, int] | None = None,
    spend_cap: str = "",
) -> Path:
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "child.process.md").write_text(CHILD.format(cost=cost_usd), encoding="utf-8")
    items = " ".join(
        f'"    - ticker: {ticker}" "      should_fail: {str(fail).lower()}"'
        for ticker, fail in tickers
    )
    spend = (
        ""
        if per_item_usd is None
        else (
            "      spend:\n"
            f"        per_item_usd: {per_item_usd}\n"
            "        source: test fixture price\n"
            f"        max_attempts: {max_attempts}\n"
        )
    )
    breaker_block = (
        ""
        if breaker is None
        else (
            "        breaker:\n"
            f"          max_failure_fraction: {breaker[0]}\n"
            f"          min_finished: {breaker[1]}\n"
        )
    )
    parent = process_dir / "parent.process.md"
    parent.write_text(
        PARENT.format(items=items, spend=spend, breaker=breaker_block, spend_cap=spend_cap),
        encoding="utf-8",
    )
    return parent


def _run(parent: Path, runs_dir: Path, run_id: str, *extra: str) -> Result:
    return CliRunner().invoke(
        app,
        [
            "run-process",
            str(parent),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            f"RUN_ID={run_id}",
            *extra,
        ],
    )


def _completed(run_dir: Path) -> list[str]:
    tasks = run_dir / STATE_DIR / "tasks" / "items"
    return sorted(
        path.parent.name
        for path in tasks.glob("*/status.yaml")
        if read_yaml_file(path).get("state") == "completed"
    )


def _dispatched(run_dir: Path) -> list[str]:
    return sorted(
        path.parent.name for path in (run_dir / STATE_DIR / "tasks" / "items").glob("*/status.yaml")
    )


def _process_status(run_dir: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", read_yaml_file(run_dir / STATE_DIR / "process-status.yaml"))


TEN = [(f"t{index:02d}", False) for index in range(10)]


# ── Pre-dispatch check ──────────────────────────────────────────────────


def test_cap_refuses_a_worst_case_above_it_before_any_item_starts(tmp_path: Path) -> None:
    parent = _write_processes(tmp_path / "p", tickers=TEN, cost_usd=0.01, per_item_usd=0.5)
    runs = tmp_path / "runs"

    result = _run(parent, runs, "refuse", "--max-spend-usd", "5")

    assert result.exit_code == 1
    run_dir = runs / "refuse"
    assert _dispatched(run_dir) == []
    assert not (run_dir / "items").exists()
    stopped = _process_status(run_dir)["steps"]["items"]["stopped"]
    assert stopped["reason"] == "spend-cap"
    assert stopped["phase"] == "pre-dispatch"
    assert stopped["actionable_items"] == 10
    assert stopped["max_attempts"] == 2
    assert stopped["worst_case_usd"] == pytest.approx(10.0)
    assert stopped["not_dispatched"] == 10
    message = stopped["message"]
    assert "10 items x 2 attempts x 0.5 USD = 10.00 USD" in message
    assert "above the 5.00 USD spend cap" in message
    assert "Raise --max-spend-usd" in message


def test_cap_passes_a_worst_case_within_it(tmp_path: Path) -> None:
    parent = _write_processes(tmp_path / "p", tickers=TEN, cost_usd=0.01, per_item_usd=0.5)
    runs = tmp_path / "runs"

    result = _run(parent, runs, "pass", "--max-spend-usd", "10")

    assert result.exit_code == 0, result.output
    run_dir = runs / "pass"
    assert len(_completed(run_dir)) == 10
    ledger = read_spend_ledger(run_dir)
    assert ledger is not None
    assert ledger.cap_usd == 10
    assert ledger.measured_usd == pytest.approx(0.1)


def test_resume_prices_only_the_items_left_to_run(tmp_path: Path) -> None:
    """A mostly complete run passes under a cap its full plan would exceed."""
    tickers = [(ticker, index >= 8) for index, (ticker, _) in enumerate(TEN)]
    parent = _write_processes(tmp_path / "p", tickers=tickers, cost_usd=0.1, per_item_usd=0.5)
    runs = tmp_path / "runs"
    marker = tmp_path / "fail-marker"
    marker.write_text("x", encoding="utf-8")

    first = _run(parent, runs, "resume", "--var", f"FAIL_MARKER={marker}")
    assert first.exit_code == 1
    run_dir = runs / "resume"
    assert len(_completed(run_dir)) == 8

    # The full plan's worst case, 10 x 2 x 0.5 = 10 USD, is above a 5 USD cap.
    fresh = _run(parent, runs, "fresh", "--max-spend-usd", "5")
    assert fresh.exit_code == 1
    assert _process_status(runs / "fresh")["steps"]["items"]["stopped"]["actionable_items"] == 10

    # The resume runs 2 items: 1.00 measured + 2 x 2 x 0.5 = 3.00 USD, within 5.
    marker.unlink()
    resumed = _run(parent, runs, "resume", "--max-spend-usd", "5", "--var", f"FAIL_MARKER={marker}")
    assert resumed.exit_code == 0, resumed.output
    assert len(_completed(run_dir)) == 10
    assert "worst case 2.00 USD plus 1.00 USD measured" in resumed.output


# ── In-run stop ─────────────────────────────────────────────────────────


def _budget_stopped_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Five items at 0.4 USD each under a 1 USD cap: three run, two stay pending."""
    tickers = [(f"s{index}", False) for index in range(5)]
    parent = _write_processes(
        tmp_path / "p", tickers=tickers, cost_usd=0.4, per_item_usd=0.1, max_attempts=1
    )
    runs = tmp_path / "runs"
    result = _run(parent, runs, "stop", "--max-spend-usd", "1")
    assert result.exit_code == 1, result.output
    return parent, runs, runs / "stop"


def test_measured_spend_at_the_cap_stops_new_items(tmp_path: Path) -> None:
    _parent, _runs, run_dir = _budget_stopped_run(tmp_path)

    assert len(_completed(run_dir)) == 3
    assert len(_dispatched(run_dir)) == 3
    status = _process_status(run_dir)
    assert status["state"] == "failed"
    stopped = status["steps"]["items"]["stopped"]
    assert stopped["reason"] == "spend-cap"
    assert stopped["phase"] == "in-run"
    assert stopped["not_dispatched"] == 2
    assert stopped["measured_usd"] == pytest.approx(1.2)
    assert status["stopped"] == [stopped]
    ledger = read_spend_ledger(run_dir)
    assert ledger is not None and ledger.measured_usd == pytest.approx(1.2)


def test_status_names_a_budget_stop(tmp_path: Path) -> None:
    _parent, _runs, run_dir = _budget_stopped_run(tmp_path)

    status = scan_run_status(run_dir)
    assert [stop.label for stop in status.dispatch_stops] == ["budget-stopped"]
    text = _format_text(status)
    assert "Status: BUDGET-STOPPED" in text
    assert "Stopped: step 'items' is budget-stopped (in-run), 2 items not dispatched" in text


def test_a_budget_stopped_run_resumes_only_with_a_higher_cap(tmp_path: Path) -> None:
    parent, runs, run_dir = _budget_stopped_run(tmp_path)
    config = read_yaml_file(run_dir / STATE_DIR / "run-config.yaml")
    assert config["max_spend_usd"] == 1.0

    # Without the flag the recorded 1 USD cap applies, and 1.20 USD is already spent.
    same = _run(parent, runs, "stop")
    assert same.exit_code == 1
    stopped = _process_status(run_dir)["steps"]["items"]["stopped"]
    assert stopped["phase"] == "pre-dispatch"
    assert stopped["measured_usd"] == pytest.approx(1.2)
    assert len(_dispatched(run_dir)) == 3

    higher = _run(parent, runs, "stop", "--max-spend-usd", "3")
    assert higher.exit_code == 0, higher.output
    assert len(_completed(run_dir)) == 5
    assert read_yaml_file(run_dir / STATE_DIR / "run-config.yaml")["max_spend_usd"] == "3.0"
    ledger = read_spend_ledger(run_dir)
    assert ledger is not None and ledger.measured_usd == pytest.approx(2.0)


def test_a_resume_without_the_logs_still_counts_the_recorded_spend(tmp_path: Path) -> None:
    parent, runs, run_dir = _budget_stopped_run(tmp_path)
    for log in run_dir.rglob("*.jsonl"):
        log.unlink()

    again = _run(parent, runs, "stop", "--max-spend-usd", "1")

    assert again.exit_code == 1
    stopped = _process_status(run_dir)["steps"]["items"]["stopped"]
    assert stopped["phase"] == "pre-dispatch"
    assert stopped["measured_usd"] == pytest.approx(1.2)
    assert len(_dispatched(run_dir)) == 3


def test_a_required_cap_refuses_a_launch_without_one(tmp_path: Path) -> None:
    parent = _write_processes(
        tmp_path / "p",
        tickers=TEN,
        cost_usd=0.01,
        per_item_usd=0.5,
        spend_cap="  spend_cap:\n    required: true\n",
    )
    runs = tmp_path / "runs"

    result = _run(parent, runs, "required")

    assert isinstance(result.exception, ValidationError)
    assert result.exception.exit_code == 2
    assert "requires a spend cap" in str(result.exception)
    assert not (runs / "required").exists()


# ── Breaker ─────────────────────────────────────────────────────────────


def test_breaker_trips_above_its_failure_fraction(tmp_path: Path) -> None:
    tickers = [("b0", True), ("b1", True), ("b2", True), ("b3", False)] + [
        (f"b{index}", False) for index in range(4, 10)
    ]
    parent = _write_processes(tmp_path / "p", tickers=tickers, cost_usd=0.0, breaker=(0.5, 4))
    runs = tmp_path / "runs"
    marker = tmp_path / "fail-marker"
    marker.write_text("x", encoding="utf-8")

    result = _run(parent, runs, "trip", "--var", f"FAIL_MARKER={marker}")

    assert result.exit_code == 1
    run_dir = runs / "trip"
    assert _dispatched(run_dir) == ["b0", "b1", "b2", "b3"]
    stopped = _process_status(run_dir)["steps"]["items"]["stopped"]
    assert stopped["reason"] == "failure-rate"
    assert (stopped["finished"], stopped["failed"], stopped["not_dispatched"]) == (4, 3, 6)
    assert scan_run_status(run_dir).dispatch_stops[0].label == "breaker-stopped"


def test_breaker_holds_at_its_failure_fraction(tmp_path: Path) -> None:
    tickers = [("h0", True), ("h1", True), ("h2", False), ("h3", False)] + [
        (f"h{index}", False) for index in range(4, 10)
    ]
    parent = _write_processes(tmp_path / "p", tickers=tickers, cost_usd=0.0, breaker=(0.5, 4))
    runs = tmp_path / "runs"
    marker = tmp_path / "fail-marker"
    marker.write_text("x", encoding="utf-8")

    result = _run(parent, runs, "hold", "--var", f"FAIL_MARKER={marker}")

    assert result.exit_code == 1
    run_dir = runs / "hold"
    assert len(_dispatched(run_dir)) == 10
    assert len(_completed(run_dir)) == 8
    assert "stopped" not in _process_status(run_dir)["steps"]["items"]


# ── Units ───────────────────────────────────────────────────────────────


def test_gate_counts_items_it_holds_back() -> None:
    gate = MappedDispatch(
        step_id="s", breaker=DispatchBreaker(max_failure_fraction=0.3, min_finished=20)
    )
    for index in range(20):
        assert gate.admit()
        gate.record(succeeded=index >= 7)
    assert not gate.admit()
    assert not gate.admit()
    stop = gate.stop
    assert stop is not None
    assert (stop.finished, stop.failed, stop.not_dispatched) == (20, 7, 2)


def test_gate_holds_at_the_boundary() -> None:
    gate = MappedDispatch(
        step_id="s", breaker=DispatchBreaker(max_failure_fraction=0.3, min_finished=20)
    )
    for index in range(20):
        assert gate.admit()
        gate.record(succeeded=index >= 6)
    assert gate.admit()
    assert gate.stop is None


def test_ledger_counts_each_log_once_across_rescans_and_compaction(tmp_path: Path) -> None:
    log_dir = tmp_path / ".logs" / "tasks" / "a"
    log_dir.mkdir(parents=True)
    log = log_dir / "a_x_2026-01-01T00-00-00.jsonl"
    log.write_text(
        '{"type":"system","subtype":"init","model":"claude-sonnet-4-5"}\n'
        '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.25}\n',
        encoding="utf-8",
    )
    ledger = SpendLedger.open(tmp_path, cap_usd=1.0)
    assert ledger.measured_usd == pytest.approx(0.25)
    assert ledger.observe(tmp_path) == pytest.approx(0.25)
    assert ledger.observe(log_dir) == pytest.approx(0.25)

    compacted = log.with_name(log.name + ".gz")
    compacted.write_bytes(gzip.compress(log.read_bytes()))
    log.unlink()
    assert ledger.observe(tmp_path) == pytest.approx(0.25)


def test_price_and_breaker_do_not_change_the_step_fingerprint() -> None:
    fan_out = FanOut(over="deps.r", bind="t", source="/r.md", bind_fields=["t"])
    plain = ResolvedStep(step_id="s", mode="composite", fan_out=fan_out)
    priced = plain.model_copy(
        update={
            "spend": StepSpend(per_item_usd=0.1, source="x"),
            "fan_out": fan_out.model_copy(
                update={"breaker": DispatchBreaker(max_failure_fraction=0.3, min_finished=20)}
            ),
        }
    )
    assert fingerprint_step(priced) == fingerprint_step(plain)


def test_composite_attempts_come_from_the_child_agent_retries(tmp_path: Path) -> None:
    child = tmp_path / "child.process.md"
    child.write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: agent-child
              defaults:
                retry: {max_retries: 1}
              steps:
                - id: decide
                  mode: agent
                  prompt_prefix: decide
                - id: check
                  mode: code
                  command: /bin/true
            ---
            Child.
            """
        ),
        encoding="utf-8",
    )
    assert _composite_item_attempts(child, step_id="s") == 2


def test_spend_requires_a_mapped_step() -> None:
    with pytest.raises(ValueError, match="requires for_each"):
        ProcessStep.model_validate(
            {
                "id": "s",
                "mode": "agent",
                "spend": {"per_item_usd": 0.1, "source": "x"},
            }
        )
