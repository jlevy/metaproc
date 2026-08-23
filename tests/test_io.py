"""Tests for metaproc.io — frontmatter loading and state I/O."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from metaproc.io import to_yaml_string
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
    end_attempt_at,
    end_status_attempt_at,
    mark_completed_at,
    mark_failed_at,
    mark_failed_synthetic_at,
    mark_running_at,
    read_attempt_history_at,
    read_status_at,
    reconcile_stale_running,
    start_attempt_at,
    write_attempt_at,
    write_result_at,
    write_status_at,
)
from metaproc.models.authored import IOSpec
from metaproc.models.runtime import (
    AttemptDisposition,
    AttemptRecord,
    MapItem,
    ResultRecord,
    StatusRecord,
    TaskAttemptRecord,
)
from metaproc.paths import ATTEMPT_FILE, ATTEMPTS_SUBDIR, STATE_DIR, TASKS_SUBDIR

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

    def test_attempt_history_retains_each_terminal_retry(self, tmp_path):
        first = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        mark_failed_at(
            tmp_path,
            error="response timeout",
            running_record=first,
            attempt_disposition=AttemptDisposition.retryable,
        )
        second = mark_running_at(
            tmp_path,
            run_id="r1",
            step_id="s1",
            item={"ticker": "AAPL"},
            attempt=1,
        )
        mark_completed_at(tmp_path, running_record=second)

        history = read_attempt_history_at(tmp_path)
        assert [record.attempt_number for record in history] == [1, 2]
        assert [record.disposition for record in history] == [
            AttemptDisposition.retryable,
            AttemptDisposition.succeeded,
        ]
        assert history[0].attempt_id != history[1].attempt_id

    def test_retry_preserves_item_metadata_for_the_same_task_address(self, tmp_path):
        item = {"ticker": "AAPL", "sector": "technology"}
        first = mark_running_at(
            tmp_path,
            run_id="r1",
            step_id="s1",
            item=item,
            item_key="AAPL",
        )
        mark_failed_at(tmp_path, error="retry", running_record=first)

        second = mark_running_at(
            tmp_path,
            run_id="r1",
            step_id="s1",
            item={"step": "s1"},
            item_key="AAPL",
        )

        assert second.item == item
        assert [record.item for record in read_attempt_history_at(tmp_path)] == [item, item]

    def test_new_attempt_refuses_a_prior_live_owner(self, tmp_path):
        first = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})

        with pytest.raises(ValueError, match=f"attempt {first.attempt_id!r} is still live"):
            mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})

        history = read_attempt_history_at(tmp_path)
        assert [record.attempt_id for record in history] == [first.attempt_id]

    def test_new_attempt_refuses_misaddressed_history(self, tmp_path):
        first = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        mark_completed_at(tmp_path, running_record=first)

        with pytest.raises(ValueError, match="does not match requested task fields run_id"):
            start_attempt_at(
                tmp_path,
                run_id="another-run",
                step_id="s1",
                item={"ticker": "AAPL"},
            )

        history = read_attempt_history_at(tmp_path)
        assert len(history) == 1

    def test_mark_running_refuses_misaddressed_legacy_status(self, tmp_path):
        write_status_at(
            tmp_path,
            StatusRecord(
                run_id="another-run",
                step_id="s1",
                item={"ticker": "AAPL"},
                state="failed",
            ),
        )

        with pytest.raises(
            ValueError, match="existing status does not match requested fields run_id"
        ):
            mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})

        assert read_attempt_history_at(tmp_path) == ()

    def test_synthetic_failure_refuses_misaddressed_legacy_status(self, tmp_path):
        write_status_at(
            tmp_path,
            StatusRecord(
                run_id="another-run",
                step_id="s1",
                item={"ticker": "AAPL"},
                state="failed",
            ),
        )

        with pytest.raises(
            ValueError, match="existing status does not match requested fields run_id"
        ):
            mark_failed_synthetic_at(
                tmp_path,
                run_id="r1",
                step_id="s1",
                item={"ticker": "AAPL"},
                error="submit failed",
            )

        assert read_attempt_history_at(tmp_path) == ()

    def test_mark_running_refuses_status_behind_latest_history(self, tmp_path):
        first = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        mark_completed_at(tmp_path, running_record=first)
        latest = start_attempt_at(
            tmp_path,
            run_id="r1",
            step_id="s1",
            item={"ticker": "AAPL"},
        )
        end_attempt_at(
            tmp_path,
            attempt_id=latest.attempt_id,
            disposition=AttemptDisposition.lost,
            error="crashed before status projection",
        )

        with pytest.raises(ValueError, match=f"does not name latest attempt {latest.attempt_id!r}"):
            mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})

        assert len(read_attempt_history_at(tmp_path)) == 2

    def test_terminal_attempt_cannot_be_rewritten(self, tmp_path):
        running = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        mark_failed_at(
            tmp_path,
            error="response timeout",
            running_record=running,
            attempt_disposition=AttemptDisposition.retryable,
        )

        assert running.attempt_id is not None
        with pytest.raises(ValueError, match="already has a different terminal fact"):
            end_attempt_at(
                tmp_path,
                attempt_id=running.attempt_id,
                disposition=AttemptDisposition.permanent,
                error="response timeout",
            )

    def test_successful_attempt_cannot_be_finalized_with_failure_fields(self, tmp_path):
        running = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        assert running.attempt_id is not None

        with pytest.raises(ValueError, match="succeeded attempt cannot carry terminal failure"):
            end_attempt_at(
                tmp_path,
                attempt_id=running.attempt_id,
                disposition=AttemptDisposition.succeeded,
                error="contradictory error",
            )

    def test_rejected_terminal_rewrite_does_not_change_status(self, tmp_path):
        running = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        failed = mark_failed_at(tmp_path, error="permanent failure", running_record=running)

        with pytest.raises(ValueError, match="already has a different terminal fact"):
            mark_completed_at(tmp_path, running_record=failed)

        status = read_status_at(tmp_path)
        assert status is not None
        assert status.state == "failed"
        assert status.error == "permanent failure"

    def test_transition_rejects_status_that_misaddresses_attempt(self, tmp_path):
        running = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        misaddressed = running.model_copy(update={"run_id": "another-run"})

        with pytest.raises(ValueError, match="does not match status"):
            mark_completed_at(tmp_path, running_record=misaddressed)

        history = read_attempt_history_at(tmp_path)
        assert len(history) == 1
        assert history[0].disposition is None

    def test_attempt_history_rejects_duplicate_attempt_numbers(self, tmp_path):
        first = mark_running_at(tmp_path, run_id="r1", step_id="s1", item={"ticker": "AAPL"})
        assert first.attempt_id is not None
        duplicate = TaskAttemptRecord(
            attempt_id="att-duplicate",
            run_id="r1",
            step_id="s1",
            item={"ticker": "AAPL"},
            attempt_number=first.attempt,
            started_at=first.started_at,
        )
        duplicate_dir = tmp_path / ATTEMPTS_SUBDIR / duplicate.attempt_id
        duplicate_dir.mkdir(parents=True)
        (duplicate_dir / ATTEMPT_FILE).write_text(
            to_yaml_string(duplicate.model_dump(mode="json", by_alias=True, exclude_none=True))
        )

        with pytest.raises(ValueError, match="duplicate attempt_number 1"):
            read_attempt_history_at(tmp_path)

    def test_attempt_history_rejects_empty_attempt_directory(self, tmp_path):
        (tmp_path / ATTEMPTS_SUBDIR / "att-incomplete").mkdir(parents=True)

        with pytest.raises(ValueError, match="missing attempt.yaml"):
            read_attempt_history_at(tmp_path)

    def test_mark_failed_synthetic(self, tmp_path):
        failed = mark_failed_synthetic_at(
            tmp_path,
            run_id="analysis-research/run-1",
            step_id="edge-candidate-ledger",
            item={"ticker": "PVH", "company": "PVH Corp."},
            attempt=2,
            item_key="PVH",
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
        history = read_attempt_history_at(tmp_path)
        assert len(history) == 1
        assert history[0].item_key == "PVH"

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
    def test_reconciliation_rejects_malformed_task_status(self, tmp_path):
        run_dir = tmp_path / "runs" / "demo"
        state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
        state_dir.mkdir(parents=True)
        (state_dir / "status.yaml").write_text("state: running\n")

        with pytest.raises(ValueError):
            reconcile_stale_running(run_dir)

    def test_reconciles_first_attempt_created_before_status_projection(self, tmp_path):
        run_dir = tmp_path / "runs" / "demo"
        state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
        attempt = start_attempt_at(
            state_dir,
            run_id="predict/demo",
            step_id="predict-ticker",
            item={"ticker": "AAPL"},
            item_key="AAPL",
        )

        assert read_status_at(state_dir) is None
        assert reconcile_stale_running(run_dir) == 1

        projected = read_status_at(state_dir)
        assert projected is not None
        assert projected.state == "failed"
        assert projected.attempt_id == attempt.attempt_id
        history = read_attempt_history_at(state_dir)
        assert [record.disposition for record in history] == [AttemptDisposition.lost]

    def test_reconciles_new_attempt_created_after_prior_terminal_status(self, tmp_path):
        run_dir = tmp_path / "runs" / "demo"
        state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
        first = mark_running_at(
            state_dir,
            run_id="predict/demo",
            step_id="predict-ticker",
            item={"ticker": "AAPL"},
            item_key="AAPL",
        )
        mark_completed_at(state_dir, running_record=first)
        orphan = start_attempt_at(
            state_dir,
            run_id="predict/demo",
            step_id="predict-ticker",
            item={"ticker": "AAPL"},
            item_key="AAPL",
        )

        assert reconcile_stale_running(run_dir) == 1

        projected = read_status_at(state_dir)
        assert projected is not None
        assert projected.state == "failed"
        assert projected.attempt_id == orphan.attempt_id
        history = read_attempt_history_at(state_dir)
        assert [record.disposition for record in history] == [
            AttemptDisposition.succeeded,
            AttemptDisposition.lost,
        ]

    def test_preserves_running_attempt_owned_by_live_step_pool(self, tmp_path):
        run_dir = tmp_path / "runs" / "demo"
        state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
        running = mark_running_at(
            state_dir,
            run_id="predict/demo",
            step_id="predict-ticker",
            item={"ticker": "AAPL"},
            item_key="AAPL",
        )
        live_pool = MagicMock()

        def _pool_for_step(_run_dir: Path, step_id: str | None = None):
            return live_pool if step_id == "predict-ticker" else None

        with (
            patch("metaproc.io.state_io._find_pool_status", side_effect=_pool_for_step),
            patch("metaproc.io.state_io.is_pool_alive", return_value=True),
        ):
            assert reconcile_stale_running(run_dir) == 0

        assert read_status_at(state_dir) == running
        history = read_attempt_history_at(state_dir)
        assert history[0].disposition is None

    @pytest.mark.parametrize(
        ("disposition", "expected_state"),
        [
            (AttemptDisposition.succeeded, "completed"),
            (AttemptDisposition.retryable, "failed"),
            (AttemptDisposition.permanent, "failed"),
        ],
    )
    def test_projects_terminal_attempt_when_status_transition_was_interrupted(
        self,
        tmp_path,
        disposition: AttemptDisposition,
        expected_state: str,
    ):
        run_dir = tmp_path / "runs" / "demo"
        state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
        running = mark_running_at(
            state_dir,
            run_id="predict/demo",
            step_id="predict-ticker",
            item={"ticker": "AAPL"},
            item_key="AAPL",
        )
        terminal_error = None if disposition is AttemptDisposition.succeeded else "attempt failed"
        end_status_attempt_at(
            state_dir,
            running,
            disposition=disposition,
            error=terminal_error,
        )

        assert read_status_at(state_dir) == running
        assert reconcile_stale_running(run_dir) == 1

        projected = read_status_at(state_dir)
        assert projected is not None
        assert projected.state == expected_state
        assert projected.attempt_id == running.attempt_id
        assert projected.completed_at is not None
        assert projected.error == terminal_error
        history = read_attempt_history_at(state_dir)
        assert [record.disposition for record in history] == [disposition]

    def test_repairs_stale_terminal_status_details(self, tmp_path):
        run_dir = tmp_path / "runs" / "demo"
        state_dir = run_dir / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
        running = mark_running_at(
            state_dir,
            run_id="predict/demo",
            step_id="predict-ticker",
            item={"ticker": "AAPL"},
            item_key="AAPL",
        )
        end_status_attempt_at(
            state_dir,
            running,
            disposition=AttemptDisposition.permanent,
            failure_class="invalid_output",
            error="canonical failure",
        )
        write_status_at(
            state_dir,
            running.model_copy(
                update={
                    "state": "failed",
                    "completed_at": running.started_at,
                    "error": "stale projection",
                }
            ),
        )

        assert reconcile_stale_running(run_dir) == 1

        projected = read_status_at(state_dir)
        assert projected is not None
        assert projected.error == "canonical failure"
        assert projected.failure_class == "invalid_output"

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
            state_dir,
            run_id="predict/demo",
            step_id="predict-ticker",
            item={"ticker": "AAPL"},
            item_key="AAPL",
        )

        reset = reconcile_stale_running(run_dir)
        assert reset == 1

        loaded = read_status_at(state_dir)
        assert loaded is not None
        assert loaded.state == "failed"
        assert loaded.error == "orphaned: pool process died while item was running"
        history = read_attempt_history_at(state_dir)
        assert [record.disposition for record in history] == [AttemptDisposition.lost]
