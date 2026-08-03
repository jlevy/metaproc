"""Automatic resource finalization and terminal recovery."""

from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.resource_report import _render_tree
from metaproc.engine.resource_finalizer import (
    _state_from_local_terminal_evidence,
    finalize_run_resources,
    recover_incomplete_resource_artifacts,
    state_for_terminal_error,
)
from metaproc.io import fmf_read_frontmatter, read_yaml_file
from metaproc.io.state_io import mark_failed_at, mark_running_at
from metaproc.models.resource_budget import FinalizationState, ResourceUsageSummary
from metaproc.models.resources import ResourcesDocument
from metaproc.osutils.memory_pressure import PressureLevel
from metaproc.paths import (
    POOL_STATUS_FILE,
    resource_usage_summary_file,
    resources_file,
    run_resource_events_file,
    run_state_dir,
)
from metaproc.runpool.process_events import ProcessEventLogger
from metaproc.runpool.status import (
    FailureCounts,
    PressureStatus,
    ProcessStatus,
    RunPoolStatus,
    write_status,
)
from metaproc.viz_loader import load_plan_bundle


def _write_process(path: Path, *, output: bool = False) -> None:
    source = (
        """\
            ---
            process:
              name: resource-finalizer-test
              resource_budgets:
                - budget_id: bud_run-wall
                  scope:
                    kind: run
                  metric: wall_time_s
                  threshold: 60
                  near_ratio: 0.8
                  posture: observe
              outputs:
                report:
                  path: "{{run.dir}}/missing.md"
                  as: path
              steps:
                - id: noop
                  mode: code
                  command: "true"
            ---
            Resource-finalizer fixture.
            """
        if output
        else """\
            ---
            process:
              name: resource-finalizer-test
              resource_budgets:
                - budget_id: bud_run-wall
                  scope:
                    kind: run
                  metric: wall_time_s
                  threshold: 60
                  near_ratio: 0.8
                  posture: observe
              steps:
                - id: noop
                  mode: code
                  command: "true"
            ---
            Resource-finalizer fixture.
            """
    )
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def _write_terminal_process_events(run_dir: Path, *, failed: int = 0) -> None:
    with ProcessEventLogger(run_dir / ".logs" / "process-events.jsonl") as events:
        events.process_start("resource-finalizer-test", "run_finalizer-test", "local", 1)
        events.item_start("noop", "noop", step_node_id="noop")
        events.item_complete("noop", "noop", elapsed_s=4.0, step_node_id="noop")
        events.process_complete(
            "resource-finalizer-test",
            "run_finalizer-test",
            completed=0 if failed else 1,
            failed=failed,
            skipped=0,
            elapsed_s=4.0,
        )


def test_finalizer_writes_machine_and_structured_human_reports_idempotently(
    tmp_path: Path,
) -> None:
    process_path = tmp_path / "test.process.md"
    _write_process(process_path)
    run_dir = tmp_path / "runs" / "run_finalizer-test"
    _write_terminal_process_events(run_dir)
    bundle = load_plan_bundle(
        process_path,
        params={"RUN_ID": "run_finalizer-test", "RUNS_DIR": str(tmp_path / "runs")},
    )

    first = finalize_run_resources(
        run_dir,
        bundle=bundle,
        run_id="run_finalizer-test",
        state=FinalizationState.COMPLETED,
        trigger="terminal",
    )
    assert re.fullmatch(r"use_[a-z0-9]{14}", first.usage_id or "")
    first_bytes = (
        resources_file(run_dir).read_bytes(),
        run_resource_events_file(run_dir).read_bytes(),
        resource_usage_summary_file(run_dir).read_bytes(),
    )
    second = finalize_run_resources(
        run_dir,
        bundle=bundle,
        run_id="run_finalizer-test",
        state=FinalizationState.COMPLETED,
        trigger="resume",
    )
    third = finalize_run_resources(
        run_dir,
        bundle=bundle,
        run_id="run_finalizer-test",
        state=FinalizationState.COMPLETED,
        trigger="rehydration",
    )

    assert first == second == third
    assert first_bytes == (
        resources_file(run_dir).read_bytes(),
        run_resource_events_file(run_dir).read_bytes(),
        resource_usage_summary_file(run_dir).read_bytes(),
    )
    assert first.finalization is not None
    assert first.finalization.state is FinalizationState.COMPLETED
    assert first.budget_evaluations[0].actual_quantity == 4
    summary = resource_usage_summary_file(run_dir).read_text()
    assert "metaproc:ResourceUsageSummary/0.1" in summary
    assert "## Provider meters" in summary
    assert "## Budgets" in summary
    summary_frontmatter = fmf_read_frontmatter(resource_usage_summary_file(run_dir))
    ResourceUsageSummary.model_validate(summary_frontmatter)
    rendered = _render_tree(first)
    assert "finalization=completed" in rendered
    assert "bud_run-wall: within" in rendered


def test_finalizer_records_timeout_and_cancellation_states(tmp_path: Path) -> None:
    process_path = tmp_path / "test.process.md"
    _write_process(process_path)
    runs_dir = tmp_path / "runs"
    bundle = load_plan_bundle(
        process_path,
        params={"RUN_ID": "run_timeout-test", "RUNS_DIR": str(runs_dir)},
    )

    timeout_dir = runs_dir / "run_timeout-test"
    _write_terminal_process_events(timeout_dir, failed=1)
    state_dir = timeout_dir / ".state" / "tasks" / "noop"
    running = mark_running_at(
        state_dir,
        run_id="run_timeout-test",
        step_id="noop",
        item={"step": "noop"},
    )
    mark_failed_at(state_dir, error="timeout after 1s", running_record=running)
    timed_out = finalize_run_resources(
        timeout_dir,
        bundle=bundle,
        run_id="run_timeout-test",
        state=FinalizationState.FAILED,
        trigger="terminal",
        terminal_error=RuntimeError("generic orchestration failure"),
    )
    assert timed_out.finalization is not None
    assert timed_out.finalization.state is FinalizationState.TIMED_OUT

    cancelled_dir = runs_dir / "run_cancelled-test"
    _write_terminal_process_events(cancelled_dir, failed=1)
    cancellation = KeyboardInterrupt()
    cancelled = finalize_run_resources(
        cancelled_dir,
        bundle=bundle,
        run_id="run_cancelled-test",
        state=state_for_terminal_error(cancellation),
        trigger="terminal",
        terminal_error=cancellation,
    )
    assert cancelled.finalization is not None
    assert cancelled.finalization.state is FinalizationState.CANCELLED


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("failed"), FinalizationState.FAILED),
        (subprocess.TimeoutExpired("command", 10), FinalizationState.TIMED_OUT),
        (KeyboardInterrupt(), FinalizationState.CANCELLED),
    ],
)
def test_terminal_errors_map_to_visible_states(
    error: BaseException,
    expected: FinalizationState,
) -> None:
    assert state_for_terminal_error(error) is expected


def test_recovery_rebuilds_missing_reports_from_persisted_events(tmp_path: Path) -> None:
    process_path = tmp_path / "test.process.md"
    _write_process(process_path)
    run_dir = tmp_path / "runs" / "run_finalizer-test"
    _write_terminal_process_events(run_dir)
    bundle = load_plan_bundle(
        process_path,
        params={"RUN_ID": "run_finalizer-test", "RUNS_DIR": str(tmp_path / "runs")},
    )
    finalized = finalize_run_resources(
        run_dir,
        bundle=bundle,
        run_id="run_finalizer-test",
        state=FinalizationState.COMPLETED,
        trigger="terminal",
    )
    run_config = run_dir / ".state" / "run-config.yaml"
    run_config.parent.mkdir(parents=True, exist_ok=True)
    run_config.write_text(
        textwrap.dedent(
            f"""\
            process: resource-finalizer-test
            process_spec: {process_path}
            run_id: run_finalizer-test
            variables:
              RUN_ID: run_finalizer-test
              RUNS_DIR: {tmp_path / "runs"}
            resource_budgets:
              - budget_id: bud_run-wall
                scope:
                  kind: run
                metric: wall_time_s
                threshold: 60
                near_ratio: 0.8
                posture: observe
            """
        )
    )
    resources_file(run_dir).unlink()
    resource_usage_summary_file(run_dir).unlink()

    recovered = recover_incomplete_resource_artifacts(run_dir, is_active=False)

    assert recovered is not None
    assert recovered.finalization is not None
    assert recovered.finalization.state is FinalizationState.COMPLETED
    assert recovered.finalization.recovered is True
    assert recovered.hierarchy_root.total_metrics.wall_time_s == 4
    assert run_resource_events_file(run_dir).exists()
    assert finalized.usage_id == recovered.usage_id


def test_recovery_detects_later_interrupted_resume(tmp_path: Path) -> None:
    process_path = tmp_path / "test.process.md"
    _write_process(process_path)
    run_dir = tmp_path / "runs" / "run_finalizer-test"
    _write_terminal_process_events(run_dir)
    bundle = load_plan_bundle(
        process_path,
        params={"RUN_ID": "run_finalizer-test", "RUNS_DIR": str(tmp_path / "runs")},
    )
    finalized = finalize_run_resources(
        run_dir,
        bundle=bundle,
        run_id="run_finalizer-test",
        state=FinalizationState.COMPLETED,
        trigger="terminal",
    )
    with ProcessEventLogger(run_dir / ".logs" / "process-events.jsonl") as events:
        events.process_start("resource-finalizer-test", "run_finalizer-test", "local", 1)

    recovered = recover_incomplete_resource_artifacts(run_dir, is_active=False)

    assert recovered is not None
    assert recovered.finalization is not None
    assert recovered.finalization.state is FinalizationState.INTERRUPTED
    assert recovered.finalization.recovered is True
    assert recovered.usage_id == finalized.usage_id


def test_status_recovery_does_not_overwrite_historical_resource_document(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "run_historical-test"
    _write_terminal_process_events(run_dir)
    historical = {
        "schema": "metaproc.resources/v1",
        "run_id": "run_historical-test",
        "generated_at": "2026-08-02T00:00:00Z",
        "source_events_path": ".logs/resource-events.jsonl",
        "hierarchy_root": {
            "node_type": "run",
            "node_id": "run_historical-test",
            "label": "historical",
        },
    }
    machine_path = resources_file(run_dir)
    machine_path.write_text(json.dumps(historical, sort_keys=True))
    before = machine_path.read_bytes()

    recovered = recover_incomplete_resource_artifacts(run_dir, is_active=False)

    assert recovered is None
    assert machine_path.read_bytes() == before


def test_status_recovery_ignores_malformed_run_config(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run_malformed-config"
    _write_terminal_process_events(run_dir)
    run_config = run_dir / ".state" / "run-config.yaml"
    run_config.parent.mkdir(parents=True, exist_ok=True)
    run_config.write_text("resource_budgets: [\n", encoding="utf-8")

    recovered = recover_incomplete_resource_artifacts(run_dir, is_active=False)

    assert recovered is None


def test_run_process_finalizes_success_failure_and_status_recovery(tmp_path: Path) -> None:
    process_path = tmp_path / "test.process.md"
    _write_process(process_path)
    runs_dir = tmp_path / "runs"
    runner = CliRunner()
    args = [
        "run-process",
        str(process_path),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        "RUN_ID=run_finalizer-test",
    ]

    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    run_dir = runs_dir / "run_finalizer-test"
    document = ResourcesDocument.model_validate_json(resources_file(run_dir).read_text())
    assert document.finalization is not None
    assert document.finalization.state is FinalizationState.COMPLETED
    config = read_yaml_file(run_dir / ".state" / "run-config.yaml")
    assert config["resource_budgets"][0]["budget_id"] == "bud_run-wall"

    resume_result = runner.invoke(app, args)
    assert resume_result.exit_code == 0, resume_result.output
    resumed = ResourcesDocument.model_validate_json(resources_file(run_dir).read_text())
    assert resumed.finalization is not None
    assert resumed.finalization.trigger == "resume"

    resources_file(run_dir).unlink()
    resource_usage_summary_file(run_dir).unlink()
    status_result = runner.invoke(app, ["status", str(run_dir), "--no-system"])
    assert status_result.exit_code == 0, status_result.output
    assert resources_file(run_dir).exists()
    assert resource_usage_summary_file(run_dir).exists()

    failing_path = tmp_path / "failing.process.md"
    _write_process(failing_path, output=True)
    fail_result = runner.invoke(
        app,
        [
            "run-process",
            str(failing_path),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            "RUN_ID=run_finalizer-failed",
        ],
    )
    assert fail_result.exit_code != 0
    failed_doc = json.loads(resources_file(runs_dir / "run_finalizer-failed").read_text())
    assert failed_doc["finalization"]["state"] == "failed"

    failed_summary = resource_usage_summary_file(runs_dir / "run_finalizer-failed")
    failed_summary.unlink()
    failed_status = runner.invoke(
        app,
        ["status", str(runs_dir / "run_finalizer-failed"), "--no-system"],
    )
    assert failed_status.exit_code == 0, failed_status.output
    recovered_failed = json.loads(resources_file(runs_dir / "run_finalizer-failed").read_text())
    assert recovered_failed["finalization"]["state"] == "failed"
    assert recovered_failed["finalization"]["terminal_error_type"] == "CLIError"


def _pool_status(
    *, updated_at: str, timeouts: int = 0, kill_reason: str | None = None
) -> RunPoolStatus:
    return RunPoolStatus(
        pool_id="p",
        pid=1,
        started_at="2026-08-02T00:00:00Z",
        updated_at=updated_at,
        backend="local",
        max_concurrency=1,
        current_concurrency=1,
        active_count=0,
        pending_count=0,
        completed_count=1,
        failed_count=0,
        killed_count=0,
        pressure=PressureStatus(
            level=PressureLevel.NORMAL, available_pct=90.0, swap_used_gb=0.0, source="test"
        ),
        failure_counts=FailureCounts(timeout=timeouts),
        recent_completions=[
            ProcessStatus(
                backend="local",
                label="item",
                started_at="2026-08-02T00:00:00Z",
                elapsed_s=1.0,
                status="killed" if kill_reason else "completed",
                kill_reason=kill_reason,
            )
        ],
    )


def test_earlier_step_timeouts_do_not_relabel_a_later_failure(tmp_path: Path) -> None:
    """Pool timeout counters are cumulative and per-step.

    A fan-out step that recorded timeouts earlier in the run must not make an
    unrelated later orchestrator failure report as `timed_out`.
    """
    run_dir = tmp_path / "run_scoped-timeout"
    state_root = run_state_dir(run_dir)

    early = state_root / "pools" / "step-a"
    early.mkdir(parents=True)
    write_status(
        early / POOL_STATUS_FILE, _pool_status(updated_at="2026-08-02T01:00:00Z", timeouts=3)
    )

    late = state_root / "pools" / "step-b"
    late.mkdir(parents=True)
    write_status(late / POOL_STATUS_FILE, _pool_status(updated_at="2026-08-02T02:00:00Z"))

    assert (
        _state_from_local_terminal_evidence(run_dir, FinalizationState.FAILED)
        is FinalizationState.FAILED
    )


def test_timeout_in_the_terminal_pool_is_still_detected(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_terminal-timeout"
    state_root = run_state_dir(run_dir)

    early = state_root / "pools" / "step-a"
    early.mkdir(parents=True)
    write_status(early / POOL_STATUS_FILE, _pool_status(updated_at="2026-08-02T01:00:00Z"))

    late = state_root / "pools" / "step-b"
    late.mkdir(parents=True)
    write_status(
        late / POOL_STATUS_FILE, _pool_status(updated_at="2026-08-02T02:00:00Z", timeouts=1)
    )

    assert (
        _state_from_local_terminal_evidence(run_dir, FinalizationState.FAILED)
        is FinalizationState.TIMED_OUT
    )
