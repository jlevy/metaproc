"""Tests for metaproc.io — frontmatter loading and state I/O."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from metaproc.io.frontmatter import (
    ENVELOPE_MAP,
    ProcessEnvelope,
    extract_items_from_envelope,
    load_frontmatter_typed,
    load_yaml_typed,
    register_envelopes,
)
from metaproc.io.state_io import (
    compute_item_dir,
    mark_completed_at,
    mark_failed_at,
    mark_failed_synthetic_at,
    mark_running_at,
    read_status_at,
    reconcile_stale_running,
    write_attempt_at,
    write_result_at,
    write_status_at,
)
from metaproc.models.authored import IOSpec
from metaproc.models.runtime import (
    AttemptRecord,
    MapItem,
    ResultRecord,
    StatusRecord,
)
from metaproc.paths import STATE_DIR, TASKS_SUBDIR

# ── Frontmatter tests ───────────────────────────────────────────


class TestProcessEnvelope:
    def test_validates_process_spec(self):
        data = {"process": {"name": "test-proc", "steps": []}}
        env = ProcessEnvelope.model_validate(data)
        assert env.process.name == "test-proc"


class TestEnvelopeRegistry:
    def test_process_registered_by_default(self):
        assert "process" in ENVELOPE_MAP

    def test_register_custom_envelope(self):
        class CustomInner(BaseModel):
            items: list[MapItem] = []

        class CustomEnvelope(BaseModel):
            custom: CustomInner

        register_envelopes({"custom": CustomEnvelope})
        assert "custom" in ENVELOPE_MAP
        # Clean up
        del ENVELOPE_MAP["custom"]


class TestLoadFrontmatterTyped:
    def test_loads_process_spec(self, tmp_path):
        f = tmp_path / "test.process.md"
        f.write_text("---\nprocess:\n  name: test\n  steps: []\n---\nBody text.\n")
        result = load_frontmatter_typed(f)
        assert isinstance(result, ProcessEnvelope)
        assert result.process.name == "test"

    def test_raises_on_no_frontmatter(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("No frontmatter here.")
        with pytest.raises(ValueError, match="no YAML frontmatter"):
            load_frontmatter_typed(f)

    def test_raises_on_unknown_type(self, tmp_path):
        f = tmp_path / "unknown.md"
        f.write_text("---\nunknown_key:\n  data: 1\n---\n")
        with pytest.raises(ValueError, match="no recognized document type"):
            load_frontmatter_typed(f)


class TestLoadYamlTyped:
    def test_loads_status_record(self, tmp_path):
        f = tmp_path / "status.yaml"
        f.write_text("run_id: r1\nstep_id: s1\nitem:\n  ticker: AAPL\nstate: completed\n")
        result = load_yaml_typed(f, StatusRecord)
        assert result.state == "completed"
        assert result.item == {"ticker": "AAPL"}


class TestExtractItemsFromEnvelope:
    def test_extracts_items(self):
        class Inner(BaseModel):
            items: list[MapItem] = []

        class Env(BaseModel):
            tickers: Inner

        env = Env(tickers=Inner(items=[MapItem.model_validate({"ticker": "AAPL"})]))
        items = extract_items_from_envelope(env)
        assert len(items) == 1
        assert items[0]["ticker"] == "AAPL"

    def test_raises_when_no_items(self):
        class NoItems(BaseModel):
            data: str = "x"

        class Env(BaseModel):
            inner: NoItems

        with pytest.raises(TypeError, match="no inner model with an 'items' list"):
            extract_items_from_envelope(Env(inner=NoItems()))


# ── State I/O tests ──────────────────────────────────────────────


class TestStateIO:
    def test_write_and_read_status(self, tmp_path):
        record = StatusRecord(run_id="r1", step_id="s1", item={"ticker": "AAPL"}, state="running")
        write_status_at(tmp_path, record)
        result = read_status_at(tmp_path)
        assert result is not None
        assert result.state == "running"
        assert result.run_id == "r1"

    def test_read_missing_status_returns_none(self, tmp_path):
        assert read_status_at(tmp_path) is None

    def test_write_attempt(self, tmp_path):
        record = AttemptRecord(
            run_id="r1",
            step_id="s1",
            item={"ticker": "AAPL"},
            params={"TAG": "v1"},
        )
        path = write_attempt_at(tmp_path, record)
        assert path.exists()

    def test_write_result(self, tmp_path):
        record = ResultRecord(run_id="r1", step_id="s1", state="completed", validated=True)
        path = write_result_at(tmp_path, record)
        assert path.exists()


class TestMarkTransitions:
    def test_mark_running(self, tmp_path):
        record = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        assert record.state == "running"
        assert record.started_at != ""

        loaded = read_status_at(tmp_path)
        assert loaded is not None
        assert loaded.state == "running"

    def test_mark_completed(self, tmp_path):
        running = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        completed = mark_completed_at(tmp_path, running_record=running)
        assert completed.state == "completed"
        assert completed.completed_at is not None

    def test_mark_failed(self, tmp_path):
        running = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        failed = mark_failed_at(tmp_path, error="timeout", running_record=running)
        assert failed.state == "failed"
        assert failed.error == "timeout"

    def test_mark_failed_synthetic(self, tmp_path):
        failed = mark_failed_synthetic_at(
            tmp_path,
            run_id="analysis-research/run-1",
            step_id="edge-candidate-ledger",
            item={"ticker": "PVH", "company": "PVH Corp."},
            attempt=2,
            error="launch failed: token refresh timeout",
        )

        assert failed.state == "failed"
        assert failed.attempt == 2
        assert failed.item["ticker"] == "PVH"
        assert failed.error == "launch failed: token refresh timeout"
        assert failed.started_at is not None
        assert failed.completed_at == failed.started_at

        loaded = read_status_at(tmp_path)
        assert loaded is not None
        assert loaded.step_id == "edge-candidate-ledger"
        assert loaded.state == "failed"
        assert loaded.error == "launch failed: token refresh timeout"

    def test_mark_completed_reads_from_disk(self, tmp_path):
        mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        completed = mark_completed_at(tmp_path)
        assert completed.state == "completed"

    def test_mark_failed_no_record_raises(self, tmp_path):
        with pytest.raises(ValueError, match="no status.yaml"):
            mark_failed_at(tmp_path, error="oops")


class TestComputeItemDir:
    def test_resolves_template(self):

        outputs = {"main": IOSpec(path="runs/{{TAG}}/{{TICKER}}/output.md")}
        variables = {"TAG": "v1", "TICKER": "AAPL"}
        result = compute_item_dir(outputs, variables)
        assert result == Path("runs/v1/AAPL")

    def test_returns_none_when_no_outputs(self):
        assert compute_item_dir({}, {}) is None

    def test_unresolved_variable_kept(self):

        outputs = {"main": IOSpec(path="runs/{{TAG}}/output.md")}
        result = compute_item_dir(outputs, {})
        assert result is not None
        assert "{{TAG}}" in str(result)


class TestReconcileStaleRunning:
    def test_ignores_run_level_status_and_reconciles_per_task_running(self, tmp_path):

        run_dir = tmp_path / "runs" / "demo"
        root_state = run_dir / STATE_DIR
        root_state.mkdir(parents=True)
        # A run-level marker file lives at <run>/.state/ but is NOT under
        # .state/tasks/, so it's invisible to per-task reconciliation.
        (root_state / "status.yaml").write_text(
            "\n".join(
                [
                    "run_id: predict/demo",
                    "step_id: scaffold-day",
                    "state: completed",
                    "attempt: 1",
                    "started_at: '2026-04-11T18:23:39'",
                    "completed_at: '2026-04-11T18:23:54'",
                    "",
                ]
            )
        )

        # A real per-task state dir under <run>/.state/tasks/<step>/<item>/.
        state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
        state_dir.mkdir(parents=True)
        mark_running_at(
            state_dir, run_id="predict/demo", step_id="predict-ticker", item={"ticker": "AAPL"}
        )

        reset = reconcile_stale_running(run_dir)
        assert reset == 1

        loaded = read_status_at(state_dir)
        assert loaded is not None
        assert loaded.state == "failed"
        assert loaded.error == "orphaned: pool process died while item was running"
