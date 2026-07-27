"""Unit tests for run-dir layout helpers in metaproc.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from metaproc.engine.pathing import compute_task_logs_dir, compute_task_state_dir
from metaproc.errors import CLIError
from metaproc.models.authored import ForEach, IOSpec, ProcessStep
from metaproc.paths import (
    DERIVED_SUBDIR,
    DISPATCH_CONFIG_CHANGES_FILE,
    LOGS_DIR,
    RESOURCE_EVENTS_FILE,
    RUN_LAYOUT_VERSION,
    RUNPOOL_SUBDIR,
    STATE_DIR,
    STEPS_SUBDIR,
    TASKS_SUBDIR,
    TOOLS_SUBDIR,
    TRACE_FILE,
    WORKERS_SUBDIR,
    dispatch_config_changes_for_read,
    dispatch_config_changes_log,
    is_v2_run_layout,
    legacy_dispatch_config_changes_log,
    legacy_runpool_step_events,
    legacy_runpool_worker_events,
    legacy_trace_out,
    legacy_worker_state_dir,
    read_metaproc_layout,
    run_logs_dir,
    run_state_dir,
    runpool_step_events,
    runpool_worker_events,
    step_logs_dir,
    step_state_dir,
    task_logs_dir,
    task_logs_parent_dir,
    task_state_dir,
    tool_log_dir,
    tool_resource_events_log,
    trace_out,
    trace_out_for_read,
    worker_logs_dir,
    worker_state_dir,
    worker_status_file,
)


def test_constants():
    assert STATE_DIR == ".state"
    assert LOGS_DIR == ".logs"
    assert STEPS_SUBDIR == "steps"
    assert TASKS_SUBDIR == "tasks"
    assert RUNPOOL_SUBDIR == "runpool"
    assert WORKERS_SUBDIR == "workers"
    assert DERIVED_SUBDIR == "derived"
    assert TOOLS_SUBDIR == "tools"
    assert TRACE_FILE == "trace.jsonl"
    assert RESOURCE_EVENTS_FILE == "resource-events.jsonl"
    assert DISPATCH_CONFIG_CHANGES_FILE == "dispatch-config-changes.jsonl"
    assert RUN_LAYOUT_VERSION == "metaproc-run-layout/2"


def test_run_state_dir():
    run_dir = Path("/tmp/runs/r1")
    assert run_state_dir(run_dir) == run_dir / ".state"


def test_run_logs_dir():
    run_dir = Path("/tmp/runs/r1")
    assert run_logs_dir(run_dir) == run_dir / ".logs"


def test_step_state_dir():
    run_dir = Path("/tmp/runs/r1")
    assert step_state_dir(run_dir, "predict") == run_dir / ".state" / "steps" / "predict"


def test_step_logs_dir():
    run_dir = Path("/tmp/runs/r1")
    assert step_logs_dir(run_dir, "predict") == run_dir / ".logs" / "runpool" / "steps" / "predict"


def test_task_state_dir():
    run_dir = Path("/tmp/runs/r1")
    expected = run_dir / ".state" / "tasks" / "predict" / "MSFT"
    assert task_state_dir(run_dir, "predict", "MSFT") == expected


def test_task_logs_dir():
    run_dir = Path("/tmp/runs/r1")
    expected = run_dir / ".logs" / "tasks" / "predict" / "MSFT"
    assert task_logs_dir(run_dir, "predict", "MSFT") == expected


def test_task_logs_parent_dir():
    run_dir = Path("/tmp/runs/r1")
    assert task_logs_parent_dir(run_dir, "predict") == run_dir / ".logs" / "tasks" / "predict"


def test_worker_paths_normalize_worker_id():
    run_dir = Path("/tmp/runs/r1")
    assert worker_state_dir(run_dir, "0") == run_dir / ".state" / "workers" / "worker-0"
    assert worker_state_dir(run_dir, "worker-1") == run_dir / ".state" / "workers" / "worker-1"
    assert worker_logs_dir(run_dir, "0") == run_dir / ".logs" / "runpool" / "workers" / "worker-0"
    assert (
        worker_logs_dir(run_dir, "worker-1")
        == run_dir / ".logs" / "runpool" / "workers" / "worker-1"
    )
    assert (
        worker_status_file(run_dir, "0")
        == run_dir / ".state" / "workers" / "worker-0" / "runpool-status.yaml"
    )


def test_append_only_stream_paths():
    run_dir = Path("/tmp/runs/r1")
    assert (
        runpool_step_events(run_dir, "predict")
        == run_dir / ".logs" / "runpool" / "steps" / "predict" / "events.jsonl"
    )
    assert (
        runpool_worker_events(run_dir, "0")
        == run_dir / ".logs" / "runpool" / "workers" / "worker-0" / "events.jsonl"
    )
    assert (
        dispatch_config_changes_log(run_dir) == run_dir / ".logs" / "dispatch-config-changes.jsonl"
    )
    assert trace_out(run_dir) == run_dir / ".logs" / "derived" / "trace.jsonl"
    assert tool_log_dir(run_dir, "arena") == run_dir / ".logs" / "tools" / "arena"
    assert (
        tool_resource_events_log(run_dir, "arena")
        == run_dir / ".logs" / "tools" / "arena" / "resource-events.jsonl"
    )


def test_legacy_paths_are_exact_stream_fallbacks():
    run_dir = Path("/tmp/runs/r1")
    assert (
        legacy_runpool_step_events(run_dir, "predict")
        == run_dir / ".logs" / "steps" / "predict" / "runpool-events.jsonl"
    )
    assert (
        legacy_runpool_worker_events(run_dir, "0")
        == run_dir / ".logs" / "worker-0" / "runpool-events.jsonl"
    )
    assert legacy_worker_state_dir(run_dir, "0") == run_dir / ".state" / "worker-0"
    assert (
        legacy_dispatch_config_changes_log(run_dir)
        == run_dir / ".state" / "dispatch-config-changes.jsonl"
    )
    assert legacy_trace_out(run_dir) == run_dir / ".logs" / "trace.jsonl"


def test_layout_marker_detection(tmp_path):
    assert read_metaproc_layout(tmp_path) is None
    assert not is_v2_run_layout(tmp_path)

    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "run-config.yaml").write_text(
        "metaproc_layout: metaproc-run-layout/2\n", encoding="utf-8"
    )

    assert read_metaproc_layout(tmp_path) == RUN_LAYOUT_VERSION
    assert is_v2_run_layout(tmp_path)


def test_layout_marker_malformed_yaml_raises_clear_error(tmp_path):
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "run-config.yaml").write_text("metaproc_layout: [\n", encoding="utf-8")

    with pytest.raises(CLIError, match="Failed to parse metaproc layout metadata"):
        read_metaproc_layout(tmp_path)


def test_exact_fallback_uses_legacy_when_unmarked_v2_stream_absent(tmp_path):
    assert trace_out_for_read(tmp_path) == legacy_trace_out(tmp_path)
    assert dispatch_config_changes_for_read(tmp_path) == legacy_dispatch_config_changes_log(
        tmp_path
    )


def test_exact_fallback_prefers_v2_stream_for_unmarked_runs_when_present(tmp_path):
    trace_out(tmp_path).parent.mkdir(parents=True)
    trace_out(tmp_path).touch()
    dispatch_config_changes_log(tmp_path).touch()

    assert trace_out_for_read(tmp_path) == trace_out(tmp_path)
    assert dispatch_config_changes_for_read(tmp_path) == dispatch_config_changes_log(tmp_path)


def test_exact_fallback_prefers_compressed_v2_stream_for_unmarked_runs(tmp_path):
    trace_out(tmp_path).parent.mkdir(parents=True)
    compressed_trace = trace_out(tmp_path).with_name(trace_out(tmp_path).name + ".gz")
    compressed_trace.touch()
    legacy_trace_out(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    legacy_trace_out(tmp_path).touch()

    assert trace_out_for_read(tmp_path) == compressed_trace


def test_v2_marker_forces_v2_read_paths_even_before_stream_exists(tmp_path):
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "run-config.yaml").write_text(
        "metaproc_layout: metaproc-run-layout/2\n", encoding="utf-8"
    )

    assert trace_out_for_read(tmp_path) == trace_out(tmp_path)
    assert dispatch_config_changes_for_read(tmp_path) == dispatch_config_changes_log(tmp_path)


def test_v2_marker_reads_compressed_v2_stream_when_present(tmp_path):
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "run-config.yaml").write_text(
        "metaproc_layout: metaproc-run-layout/2\n", encoding="utf-8"
    )
    trace_out(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    compressed_trace = trace_out(tmp_path).with_name(trace_out(tmp_path).name + ".gz")
    compressed_trace.touch()

    assert trace_out_for_read(tmp_path) == compressed_trace


def test_helpers_are_pure():
    """Helpers must not create directories on disk; they only return Path objects."""
    run_dir = Path("/tmp/this/path/does/not/exist/runs/r1")
    # All four return Paths without touching the filesystem.
    step_state_dir(run_dir, "x")
    step_logs_dir(run_dir, "x")
    task_state_dir(run_dir, "x", "y")
    task_logs_dir(run_dir, "x", "y")
    assert not run_dir.exists()


def test_step_and_task_dirs_are_siblings_of_run_level():
    """A run dir should compose to: .state/{<run files>, steps/, tasks/}."""
    run_dir = Path("/tmp/r")
    run_state = run_state_dir(run_dir)
    assert step_state_dir(run_dir, "s").parent.parent == run_state
    assert task_state_dir(run_dir, "s", "k").parent.parent.parent == run_state


def test_compute_task_state_dir_fan_out():

    step = ProcessStep(
        id="research-step",
        mode="agent",
        for_each=ForEach(
            over="deps.tickers", bind="ticker", bind_fields=["ticker"], key="{{ticker}}"
        ),
        outputs={"x": IOSpec(path="ignored.md")},
    )
    run_dir = Path("/tmp/r")
    result = compute_task_state_dir(run_dir, step, {"ticker": "MSFT"})
    assert result == run_dir / ".state" / "tasks" / "research-step" / "MSFT"


def test_compute_task_state_dir_non_fan_out():

    step = ProcessStep(
        id="scaffold",
        mode="code",
        handler="x.py:run",
        outputs={"x": IOSpec(path="x.md")},
    )
    run_dir = Path("/tmp/r")
    result = compute_task_state_dir(run_dir, step, {})
    assert result == run_dir / ".state" / "tasks" / "scaffold"


def test_compute_task_logs_dir_fan_out():

    step = ProcessStep(
        id="research-step",
        mode="agent",
        for_each=ForEach(
            over="deps.tickers", bind="ticker", bind_fields=["ticker"], key="{{ticker}}"
        ),
        outputs={"x": IOSpec(path="ignored.md")},
    )
    run_dir = Path("/tmp/r")
    result = compute_task_logs_dir(run_dir, step, {"ticker": "MSFT"})
    assert result == run_dir / ".logs" / "tasks" / "research-step" / "MSFT"
