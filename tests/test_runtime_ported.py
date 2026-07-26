"""Tests for runtime models and .state/ I/O — ported from example_workflow.

Validates StatusRecord/AttemptRecord/ResultRecord models,
.state/ atomic I/O, harness transition helpers, and MapItem.
Domain-specific tests (TickerItem, TERMINAL_PROGRESS_STATUSES,
extract from earnings envelope, refresh_progress_summary) are skipped.
"""

from __future__ import annotations

import textwrap
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError

from metaproc.engine.discovery import discover_items_from_source
from metaproc.io.frontmatter import ENVELOPE_MAP, extract_items_from_envelope
from metaproc.io.state_io import (
    compute_item_dir,
    mark_completed_at,
    mark_failed_at,
    mark_running_at,
    read_status_at,
    write_attempt_at,
    write_result_at,
    write_status_at,
)
from metaproc.models import runtime as _rt
from metaproc.models.authored import ForEach, IOSpec, ProcessStep, StepStatus
from metaproc.models.runtime import (
    AttemptRecord,
    MapItem,
    ResultRecord,
    StatusRecord,
    register_terminal_statuses,
)
from metaproc.paths import STATE_DIR, TASKS_SUBDIR


@pytest.fixture(autouse=True, scope="module")
def _restore_terminal_statuses():
    """Save and restore _TERMINAL_STATUSES so register_terminal_statuses() calls don't leak."""

    saved = set(_rt._TERMINAL_STATUSES)
    yield
    _rt._TERMINAL_STATUSES.clear()
    _rt._TERMINAL_STATUSES.update(saved)


# ── StepStatus realignment ─────────────────────────────────────────


def test_step_status_includes_new_values():
    """StepStatus allows running, completed, cached."""

    for state in ("pending", "running", "completed", "failed", "cached"):
        record = StatusRecord(
            run_id="test",
            step_id="s1",
            item={"ticker": "AAPL"},
            state=state,
            attempt=1,
            started_at="2026-03-24T12:00:00",
        )
        assert record.state == state


def test_step_status_rejects_old_values():
    """StepStatus rejects in_progress, done, skipped."""

    for old_state in ("in_progress", "done", "skipped"):
        with pytest.raises(ValidationError):
            StatusRecord.model_validate(
                {
                    "run_id": "test",
                    "step_id": "s1",
                    "item": {"ticker": "AAPL"},
                    "state": old_state,
                    "attempt": 1,
                    "started_at": "2026-03-24T12:00:00",
                }
            )


# ── StatusRecord states ────────────────────────────────────────────


def test_status_record_represents_all_states():
    """StatusRecord captures running, completed, and failed states with correct defaults."""

    running = StatusRecord(
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        item={"ticker": "AAPL", "sector": "technology"},
        state="running",
        attempt=1,
        started_at="2026-03-24T12:00:00",
    )
    assert running.state == "running"
    assert running.item["ticker"] == "AAPL"
    assert running.completed_at is None
    assert running.error is None

    completed = StatusRecord(
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        item={"ticker": "AAPL"},
        state="completed",
        attempt=1,
        started_at="2026-03-24T12:00:00",
        completed_at="2026-03-24T12:05:00",
    )
    assert completed.state == "completed"
    assert completed.completed_at == "2026-03-24T12:05:00"

    failed = StatusRecord(
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        item={"ticker": "AAPL"},
        state="failed",
        attempt=2,
        started_at="2026-03-24T12:00:00",
        completed_at="2026-03-24T12:03:00",
        error="validation failed: missing prediction.yaml",
    )
    assert failed.state == "failed"
    assert failed.attempt == 2
    assert failed.error is not None
    assert "validation failed" in failed.error


# ── AttemptRecord / ResultRecord ──────────────────────────────────


def test_attempt_and_result_records_capture_fields():
    """AttemptRecord and ResultRecord capture their essential fields."""

    attempt = AttemptRecord.model_validate(
        {
            "run_id": "predict/2026-03-24",
            "step_id": "predict-ticker",
            "item": {"ticker": "AAPL", "sector": "technology"},
            "params": {"DATE": "2026-03-24"},
            "inputs": {"runbook": "process/predict/predict-ticker.runbook.md"},
            "outputs": {
                "prediction": "runs/predict/2026-03-24/technology/AAPL/prediction.yaml",
            },
            "runtime": {
                "adapter": "claude-code-cli",
                "model": "sonnet",
                "timeout_s": 900,
            },
        }
    )
    assert attempt.item["ticker"] == "AAPL"
    assert attempt.params["DATE"] == "2026-03-24"
    assert attempt.runtime["adapter"] == "claude-code-cli"

    completed = ResultRecord(
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        state="completed",
        validated=True,
        outputs={"prediction": "runs/predict/2026-03-24/technology/AAPL/prediction.yaml"},
        published_at="2026-03-24T12:05:00",
    )
    assert completed.state == "completed"
    assert completed.validated is True
    assert "prediction" in completed.outputs

    failed = ResultRecord(
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        state="failed",
        validated=False,
        outputs={},
        published_at="2026-03-24T12:03:00",
    )
    assert failed.state == "failed"
    assert failed.validated is False


# ── .state/ I/O ────────────────────────────────────────────────────


def test_write_status_at_creates_state_dir(tmp_path):
    """write_status_at creates the state directory and status.yaml directly."""

    state_dir = tmp_path / "technology" / "AAPL"
    state_dir.mkdir(parents=True)

    record = StatusRecord(
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        item={"ticker": "AAPL", "sector": "technology"},
        state="running",
        attempt=1,
        started_at="2026-03-24T12:00:00",
    )
    write_status_at(state_dir, record)

    assert state_dir.is_dir()
    assert (state_dir / "status.yaml").exists()


def test_read_item_status_returns_none_when_missing(tmp_path):
    """read_item_status returns None when .state/status.yaml doesn't exist."""

    item_dir = tmp_path / "AAPL"
    item_dir.mkdir()
    assert read_status_at(item_dir) is None


def test_write_read_status_roundtrip(tmp_path):
    """Write a status then read it back."""

    state_dir = tmp_path / "AAPL"
    state_dir.mkdir()

    original = StatusRecord(
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        item={"ticker": "AAPL", "sector": "technology"},
        state="running",
        attempt=1,
        started_at="2026-03-24T12:00:00",
    )
    write_status_at(state_dir, original)
    loaded = read_status_at(state_dir)

    assert loaded is not None
    assert loaded.run_id == original.run_id
    assert loaded.state == "running"
    assert loaded.item["ticker"] == "AAPL"


def test_write_attempt_creates_file(tmp_path):

    state_dir = tmp_path / "AAPL"
    state_dir.mkdir()

    record = AttemptRecord(
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        item={"ticker": "AAPL"},
        params={"DATE": "2026-03-24"},
        inputs={},
        outputs={},
        runtime={"adapter": "claude-code-cli"},
    )
    write_attempt_at(state_dir, record)
    assert (state_dir / "attempt.yaml").exists()


def test_write_result_creates_file(tmp_path):

    state_dir = tmp_path / "AAPL"
    state_dir.mkdir()

    record = ResultRecord(
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        state="completed",
        validated=True,
        outputs={"prediction": "path/to/prediction.yaml"},
        published_at="2026-03-24T12:05:00",
    )
    write_result_at(state_dir, record)
    assert (state_dir / "result.yaml").exists()


# ── Harness transition helpers ──────────────────────────────────────


def test_mark_running_writes_status(tmp_path):

    item_dir = tmp_path / "AAPL"
    item_dir.mkdir()

    record = mark_running_at(
        item_dir,
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        item={"ticker": "AAPL", "sector": "technology"},
    )
    assert record.state == "running"
    assert record.attempt == 1

    loaded = read_status_at(item_dir)
    assert loaded is not None
    assert loaded.state == "running"
    assert loaded.item["ticker"] == "AAPL"


def test_mark_completed_transitions(tmp_path):

    item_dir = tmp_path / "AAPL"
    item_dir.mkdir()

    mark_running_at(
        item_dir,
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        item={"ticker": "AAPL"},
    )
    completed = mark_completed_at(item_dir)
    assert completed.state == "completed"
    assert completed.completed_at is not None
    assert completed.run_id == "predict/2026-03-24"

    loaded = read_status_at(item_dir)
    assert loaded is not None
    assert loaded.state == "completed"


def test_mark_failed_transitions_with_error(tmp_path):

    item_dir = tmp_path / "AAPL"
    item_dir.mkdir()

    mark_running_at(
        item_dir,
        run_id="predict/2026-03-24",
        step_id="predict-ticker",
        item={"ticker": "AAPL"},
    )
    failed = mark_failed_at(item_dir, error="exit code 1")
    assert failed.state == "failed"
    assert failed.error == "exit code 1"

    loaded = read_status_at(item_dir)
    assert loaded is not None
    assert loaded.state == "failed"
    assert loaded.error == "exit code 1"


def test_mark_completed_raises_without_status(tmp_path):

    item_dir = tmp_path / "AAPL"
    item_dir.mkdir()

    with pytest.raises(ValueError, match=r"no status\.yaml"):
        mark_completed_at(item_dir)


def test_compute_item_dir():

    outputs = {
        "prediction": IOSpec(path="runs/predict/{{DATE}}/{{sector}}/{{ticker}}/prediction.yaml"),
    }
    variables = {"DATE": "2026-03-24", "sector": "technology", "ticker": "AAPL"}
    item_dir = compute_item_dir(outputs, variables)
    assert item_dir == Path("runs/predict/2026-03-24/technology/AAPL")


def test_compute_item_dir_no_outputs():

    assert compute_item_dir({}, {}) is None


# ── Fan-out discovery with .state/ join ─────────────────────────────


def _setup_tickers_envelope():
    """Register a tickers envelope for discovery tests."""

    if "tickers" not in ENVELOPE_MAP:  # noqa: SIM108 — side-effect guard

        class TickersFrontmatter(BaseModel):
            date: str = ""
            process: str = ""
            items: list[MapItem] = Field(default_factory=list)
            model_config = {"extra": "allow"}

        class TickersEnvelope(BaseModel):
            tickers: TickersFrontmatter

        ENVELOPE_MAP["tickers"] = TickersEnvelope
        register_terminal_statuses(frozenset({"done", "skipped"}))


def _write_progress(path: Path, items_yaml: str) -> None:
    """Write a tickers.md with given items block."""
    _setup_tickers_envelope()
    path.parent.mkdir(parents=True, exist_ok=True)
    items_block = textwrap.indent(textwrap.dedent(items_yaml).strip(), "    ")
    content = f'---\ntickers:\n  date: "2026-03-24"\n  process: predict\n  items:\n{items_block}\n---\n# Tickers\n'
    path.write_text(content)


def test_discover_joins_state_completed_filters_item(tmp_path):
    """Items with status.yaml = completed at the new per-task state path are filtered out."""

    progress_path = tmp_path / "progress.md"
    _write_progress(
        progress_path,
        """\
        - ticker: AAPL
          sector: technology
          status: pending
        - ticker: NVDA
          sector: technology
          status: pending
        """,
    )

    # AAPL has its artifact and a completed per-task status record.
    aapl_dir = tmp_path / "technology" / "AAPL"
    aapl_dir.mkdir(parents=True)
    (aapl_dir / "prediction.yaml").write_text("prediction: placeholder\n")
    aapl_state = tmp_path / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
    aapl_state.mkdir(parents=True)
    write_status_at(
        aapl_state,
        StatusRecord(
            run_id="predict/2026-03-24",
            step_id="predict-ticker",
            item={"ticker": "AAPL", "sector": "technology"},
            state="completed",
            attempt=1,
            started_at="2026-03-24T12:00:00",
        ),
    )

    step_outputs = {
        "prediction": IOSpec(path=f"{tmp_path}/{{{{sector}}}}/{{{{ticker}}}}/prediction.yaml"),
    }
    step_def = ProcessStep(
        id="predict-ticker",
        mode="agent",
        for_each=ForEach(
            over="progress",
            bind="ticker",
            bind_fields=["ticker", "sector"],
            key="{{ticker}}",
        ),
        outputs=step_outputs,
    )
    discovery = discover_items_from_source(
        progress_path,
        step_def,
        output_paths=step_outputs,
        params={},
        run_dir=tmp_path,
    )
    assert len(discovery.actionable_contexts) == 1  # NVDA only
    assert discovery.actionable_contexts[0]["ticker"] == "NVDA"
    assert len(discovery.filtered_items) == 1  # AAPL filtered


def test_discover_joins_state_failed_retries_item(tmp_path):
    """Items with .state/status.yaml = failed are actionable (retry)."""

    progress_path = tmp_path / "progress.md"
    _write_progress(
        progress_path,
        """\
        - ticker: AAPL
          sector: technology
          status: pending
        """,
    )

    # AAPL has .state/status.yaml = failed -> should be retried
    aapl_dir = tmp_path / "technology" / "AAPL"
    aapl_dir.mkdir(parents=True)
    aapl_state = tmp_path / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
    aapl_state.mkdir(parents=True)
    write_status_at(
        aapl_state,
        StatusRecord(
            run_id="predict/2026-03-24",
            step_id="predict-ticker",
            item={"ticker": "AAPL", "sector": "technology"},
            state="failed",
            attempt=1,
            started_at="2026-03-24T12:00:00",
            error="exit code 1",
        ),
    )

    step_outputs = {
        "prediction": IOSpec(path=f"{tmp_path}/{{{{sector}}}}/{{{{ticker}}}}/prediction.yaml"),
    }
    step_def = ProcessStep(
        id="predict-ticker",
        mode="agent",
        for_each=ForEach(
            over="progress", bind="ticker", bind_fields=["ticker", "sector"], key="{{ticker}}"
        ),
        outputs=step_outputs,
    )
    discovery = discover_items_from_source(
        progress_path,
        step_def,
        output_paths=step_outputs,
        params={},
        run_dir=tmp_path,
    )
    assert len(discovery.actionable_contexts) == 1  # AAPL retried
    assert discovery.actionable_contexts[0]["ticker"] == "AAPL"
    assert len(discovery.filtered_items) == 0


def test_discover_revalidates_completed_with_validated_outputs_policy(tmp_path):
    """reuse_policy=validated_outputs: completed items with missing outputs are demoted to actionable.

    Scenario: AAPL was marked completed on a prior run, but its declared output file
    no longer exists (e.g. deleted, wrong path, different environment). The default
    reuse_policy is 'validated_outputs' — discovery must re-validate and retry.
    """

    progress_path = tmp_path / "progress.md"
    _write_progress(
        progress_path,
        """\
        - ticker: AAPL
          sector: technology
          status: pending
        """,
    )

    # AAPL marked completed, but prediction.yaml does NOT exist on disk
    aapl_dir = tmp_path / "technology" / "AAPL"
    aapl_dir.mkdir(parents=True)
    aapl_state = tmp_path / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
    aapl_state.mkdir(parents=True)
    write_status_at(
        aapl_state,
        StatusRecord(
            run_id="predict/2026-03-24",
            step_id="predict-ticker",
            item={"ticker": "AAPL", "sector": "technology"},
            state="completed",
            attempt=1,
            started_at="2026-03-24T12:00:00",
        ),
    )

    step_outputs = {
        "prediction": IOSpec(path=f"{tmp_path}/{{{{sector}}}}/{{{{ticker}}}}/prediction.yaml"),
    }
    step_def = ProcessStep(
        id="predict-ticker",
        mode="agent",
        for_each=ForEach(
            over="progress", bind="ticker", bind_fields=["ticker", "sector"], key="{{ticker}}"
        ),
        outputs=step_outputs,
    )
    discovery = discover_items_from_source(
        progress_path,
        step_def,
        output_paths=step_outputs,
        params={},
        reuse_policy="validated_outputs",
        run_dir=tmp_path,
    )
    # AAPL demoted to actionable because prediction.yaml is missing
    assert len(discovery.actionable_contexts) == 1
    assert discovery.actionable_contexts[0]["ticker"] == "AAPL"
    assert len(discovery.filtered_items) == 0


def test_discover_trust_state_skips_revalidation(tmp_path):
    """reuse_policy=trust_state: completed items are filtered without checking outputs."""

    progress_path = tmp_path / "progress.md"
    _write_progress(
        progress_path,
        """\
        - ticker: AAPL
          sector: technology
          status: pending
        """,
    )

    aapl_dir = tmp_path / "technology" / "AAPL"
    aapl_dir.mkdir(parents=True)
    aapl_state = tmp_path / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
    aapl_state.mkdir(parents=True)
    write_status_at(
        aapl_state,
        StatusRecord(
            run_id="predict/2026-03-24",
            step_id="predict-ticker",
            item={"ticker": "AAPL", "sector": "technology"},
            state="completed",
            attempt=1,
            started_at="2026-03-24T12:00:00",
        ),
    )

    step_outputs = {
        "prediction": IOSpec(path=f"{tmp_path}/{{{{sector}}}}/{{{{ticker}}}}/prediction.yaml"),
    }
    step_def = ProcessStep(
        id="predict-ticker",
        mode="agent",
        for_each=ForEach(
            over="progress", bind="ticker", bind_fields=["ticker", "sector"], key="{{ticker}}"
        ),
        outputs=step_outputs,
    )
    discovery = discover_items_from_source(
        progress_path,
        step_def,
        output_paths=step_outputs,
        params={},
        reuse_policy="trust_state",
        run_dir=tmp_path,
    )
    # AAPL filtered even though output missing — trust_state skips re-validation
    assert len(discovery.actionable_contexts) == 0
    assert len(discovery.filtered_items) == 1
    assert discovery.filtered_items[0]["ticker"] == "AAPL"


def test_discover_validated_outputs_passes_when_output_exists(tmp_path):
    """reuse_policy=validated_outputs: completed items with valid outputs stay filtered."""

    progress_path = tmp_path / "progress.md"
    _write_progress(
        progress_path,
        """\
        - ticker: AAPL
          sector: technology
          status: pending
        """,
    )

    aapl_dir = tmp_path / "technology" / "AAPL"
    aapl_dir.mkdir(parents=True)
    aapl_state = tmp_path / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
    aapl_state.mkdir(parents=True)
    # Create the declared output — validation should pass
    (aapl_dir / "prediction.yaml").write_text("prediction: placeholder\n")
    write_status_at(
        aapl_state,
        StatusRecord(
            run_id="predict/2026-03-24",
            step_id="predict-ticker",
            item={"ticker": "AAPL", "sector": "technology"},
            state="completed",
            attempt=1,
            started_at="2026-03-24T12:00:00",
        ),
    )

    step_outputs = {
        "prediction": IOSpec(path=f"{tmp_path}/{{{{sector}}}}/{{{{ticker}}}}/prediction.yaml"),
    }
    step_def = ProcessStep(
        id="predict-ticker",
        mode="agent",
        for_each=ForEach(
            over="progress", bind="ticker", bind_fields=["ticker", "sector"], key="{{ticker}}"
        ),
        outputs=step_outputs,
    )
    discovery = discover_items_from_source(
        progress_path,
        step_def,
        output_paths=step_outputs,
        params={},
        reuse_policy="validated_outputs",
        run_dir=tmp_path,
    )
    assert len(discovery.actionable_contexts) == 0
    assert len(discovery.filtered_items) == 1
    assert discovery.filtered_items[0]["ticker"] == "AAPL"


def test_discover_joins_state_running_filters_item(tmp_path):
    """Items with .state/status.yaml = running are filtered."""

    progress_path = tmp_path / "progress.md"
    _write_progress(
        progress_path,
        """\
        - ticker: AAPL
          sector: technology
          status: pending
        - ticker: NVDA
          sector: technology
          status: pending
        """,
    )

    # AAPL has .state/status.yaml = running -> filtered (no stale detection yet)
    aapl_dir = tmp_path / "technology" / "AAPL"
    aapl_dir.mkdir(parents=True)
    aapl_state = tmp_path / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
    aapl_state.mkdir(parents=True)
    write_status_at(
        aapl_state,
        StatusRecord(
            run_id="predict/2026-03-24",
            step_id="predict-ticker",
            item={"ticker": "AAPL", "sector": "technology"},
            state="running",
            attempt=1,
            started_at="2026-03-24T12:00:00",
        ),
    )

    step_outputs = {
        "prediction": IOSpec(path=f"{tmp_path}/{{{{sector}}}}/{{{{ticker}}}}/prediction.yaml"),
    }
    step_def = ProcessStep(
        id="predict-ticker",
        mode="agent",
        for_each=ForEach(
            over="progress", bind="ticker", bind_fields=["ticker", "sector"], key="{{ticker}}"
        ),
        outputs=step_outputs,
    )
    discovery = discover_items_from_source(
        progress_path,
        step_def,
        output_paths=step_outputs,
        params={},
        run_dir=tmp_path,
    )
    # AAPL is filtered (running, no stale detection); only NVDA is actionable
    assert len(discovery.actionable_contexts) == 1
    assert discovery.actionable_contexts[0]["ticker"] == "NVDA"
    assert len(discovery.filtered_items) == 1
    assert discovery.filtered_items[0]["ticker"] == "AAPL"
    assert discovery.filtered_items[0]["reason"] == "running"


def test_discover_backward_compat_no_output_paths(tmp_path):
    """Without output_paths, falls back to progress.md status filtering."""

    progress_path = tmp_path / "progress.md"
    _write_progress(
        progress_path,
        """\
        - ticker: AAPL
          sector: technology
          status: pending
        - ticker: NVDA
          sector: technology
          status: done
        """,
    )

    step_def = ProcessStep(
        id="s1",
        mode="agent",
        for_each=ForEach(
            over="progress", bind="ticker", bind_fields=["ticker", "sector"], key="{{ticker}}"
        ),
    )
    discovery = discover_items_from_source(progress_path, step_def)
    assert len(discovery.actionable_contexts) == 1  # AAPL
    assert len(discovery.filtered_items) == 1  # NVDA (done)


def test_discover_state_overrides_progress_status(tmp_path):
    """State directory takes precedence over progress.md status field."""

    progress_path = tmp_path / "progress.md"
    # progress.md says pending, but .state/ says completed
    _write_progress(
        progress_path,
        """\
        - ticker: AAPL
          sector: technology
          status: pending
        """,
    )

    aapl_dir = tmp_path / "technology" / "AAPL"
    aapl_dir.mkdir(parents=True)
    (aapl_dir / "prediction.yaml").write_text("prediction: placeholder\n")
    aapl_state = tmp_path / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
    aapl_state.mkdir(parents=True)
    write_status_at(
        aapl_state,
        StatusRecord(
            run_id="predict/2026-03-24",
            step_id="predict-ticker",
            item={"ticker": "AAPL", "sector": "technology"},
            state="completed",
            attempt=1,
            started_at="2026-03-24T12:00:00",
        ),
    )

    step_outputs = {
        "prediction": IOSpec(path=f"{tmp_path}/{{{{sector}}}}/{{{{ticker}}}}/prediction.yaml"),
    }
    step_def = ProcessStep(
        id="predict-ticker",
        mode="agent",
        for_each=ForEach(
            over="progress", bind="ticker", bind_fields=["ticker", "sector"], key="{{ticker}}"
        ),
        outputs=step_outputs,
    )
    discovery = discover_items_from_source(
        progress_path,
        step_def,
        output_paths=step_outputs,
        params={},
        run_dir=tmp_path,
    )
    # State says completed -> filtered even though progress says pending
    assert len(discovery.actionable_contexts) == 0
    assert len(discovery.filtered_items) == 1


def test_discover_ignores_completed_status_from_different_step(tmp_path):
    """A completed status only blocks the same step, not a later step sharing the item dir."""

    progress_path = tmp_path / "progress.md"
    _write_progress(
        progress_path,
        """\
        - ticker: AAPL
          sector: technology
          status: pending
        """,
    )

    aapl_dir = tmp_path / "technology" / "AAPL"
    aapl_dir.mkdir(parents=True)
    aapl_state = tmp_path / STATE_DIR / TASKS_SUBDIR / "predict-ticker" / "AAPL"
    aapl_state.mkdir(parents=True)
    write_status_at(
        aapl_state,
        StatusRecord(
            run_id="retro/2026-03-24",
            step_id="predict-retro",
            item={"ticker": "AAPL", "sector": "technology"},
            state="completed",
            attempt=1,
            started_at="2026-03-24T12:00:00",
        ),
    )

    step_outputs = {
        "integrity_check": IOSpec(
            path=f"{tmp_path}/{{{{sector}}}}/{{{{ticker}}}}/integrity-check.md"
        ),
    }
    step_def = ProcessStep(
        id="integrity-check",
        mode="agent",
        for_each=ForEach(
            over="progress", bind="ticker", bind_fields=["ticker", "sector"], key="{{ticker}}"
        ),
        outputs=step_outputs,
    )
    discovery = discover_items_from_source(
        progress_path,
        step_def,
        output_paths=step_outputs,
        params={},
        run_dir=tmp_path,
    )

    assert len(discovery.actionable_contexts) == 1
    assert discovery.actionable_contexts[0]["ticker"] == "AAPL"
    assert len(discovery.filtered_items) == 0


def test_discover_preserves_authored_field_case(tmp_path):
    """Fan-out discovery projects the declared key names exactly as authored."""

    progress_path = tmp_path / "progress.md"
    _write_progress(
        progress_path,
        """\
        - Ticker: AAPL
          Sector: technology
        """,
    )

    step_def = ProcessStep(
        id="predict-ticker",
        mode="agent",
        for_each=ForEach.model_validate(
            {"over": "progress", "bind": "Ticker", "bind_fields": ["Ticker", "Sector"]}
        ),
    )
    discovery = discover_items_from_source(progress_path, step_def)

    assert discovery.item_key == "Ticker"
    assert discovery.item_fields == ["Ticker", "Sector"]
    assert discovery.actionable_contexts == [{"Ticker": "AAPL", "Sector": "technology"}]


# ── MapItem ──────────────────────────────────────────────────────


def test_map_item_accepts_arbitrary_fields():
    """MapItem is the framework's generic item -- accepts any fields."""

    item = MapItem.model_validate({"ticker": "AAPL", "sector": "technology", "custom": "value"})
    dumped = item.model_dump()
    assert dumped["ticker"] == "AAPL"
    assert dumped["custom"] == "value"


# ── extract_items_from_envelope ──────────────────────────────────


def test_extract_items_from_envelope_no_items_raises():
    """extract_items_from_envelope raises TypeError for envelopes without items."""

    class EmptyEnvelope(BaseModel):
        name: str = "test"

    with pytest.raises(TypeError, match="no inner model"):
        extract_items_from_envelope(EmptyEnvelope())


def test_extract_items_from_generic_envelope():
    """extract_items_from_envelope works with any envelope that has items."""

    class CustomFrontmatter(BaseModel):
        items: list[MapItem]

    class CustomEnvelope(BaseModel):
        custom: CustomFrontmatter

    envelope = CustomEnvelope(
        custom=CustomFrontmatter(items=[MapItem.model_validate({"doc_id": "abc", "author": "jl"})])
    )
    items = extract_items_from_envelope(envelope)
    assert len(items) == 1
    assert items[0]["doc_id"] == "abc"


# ── StepStatus type ─────────────────────────────────────────────


def test_old_step_status_values_gone():
    """Framework StepStatus should not include in_progress, done, skipped."""
    args = typing.get_args(StepStatus)
    assert "in_progress" not in args
    assert "done" not in args
    assert "skipped" not in args
    assert "running" in args
    assert "completed" in args
    assert "cached" in args
