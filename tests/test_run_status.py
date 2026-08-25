"""Tests for metaproc.engine.run_status — run status scanning and aggregation."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from metaproc.engine.dep_state import fingerprint_step
from metaproc.engine.run_status import (
    FailedItem,
    RetryingItem,
    RunStatus,
    TimingStats,
    _measure_subprocesses,
    check_completion,
    compute_progress,
    compute_timing,
    detect_variants,
    read_items_total,
    scan_run_status,
    scan_variant_states,
    wait_for_completion,
)
from metaproc.io.state_io import write_status_at
from metaproc.models.authored import IOSpec, ProgressCounts, StepStatus
from metaproc.models.plan import Plan, ResolvedAdapter, ResolvedStep
from metaproc.models.runtime import StatusRecord, StepState
from metaproc.paths import STATE_DIR, TASKS_SUBDIR

# ── Helpers ──────────────────────────────────────────────────────


def _make_status(
    state: StepStatus = "completed",
    item_name: str = "AAPL-2025Q4",
    attempt: int = 1,
    started_at: str = "2026-04-04T01:00:00",
    completed_at: str | None = "2026-04-04T01:10:00",
    error: str | None = None,
) -> StatusRecord:
    return StatusRecord(
        run_id="run-1",
        step_id="generate-record",
        item={"TICKER_QUARTER": item_name},
        state=state,
        attempt=attempt,
        started_at=started_at,
        completed_at=completed_at,
        error=error,
    )


def _write_item(variant_dir: Path, item_name: str, **kwargs: object) -> Path:
    """Create a per-task state directory with status.yaml directly inside.

    Translates a legacy-style ``variant_dir`` (e.g. ``run_dir/pi-cli-deepseek``)
    to the new per-task state location: ``run_dir/.state/tasks/<variant_name>/<item_name>/``.
    """
    # variant_dir like /tmp/.../run-name/pi-cli-deepseek → run_dir = /tmp/.../run-name
    run_dir = variant_dir.parent
    state_dir = run_dir / ".state" / "tasks" / variant_dir.name / item_name
    state_dir.mkdir(parents=True, exist_ok=True)
    record = _make_status(item_name=item_name, **kwargs)  # pyright: ignore[reportArgumentType]
    write_status_at(state_dir, record)
    return state_dir


# ── TimingStats ──────────────────────────────────────────────────


class TestTimingStats:
    def test_fields(self) -> None:
        ts = TimingStats(
            avg_seconds=600.0,
            min_seconds=300.0,
            max_seconds=900.0,
            elapsed=timedelta(seconds=1200),
            eta_seconds=300.0,
        )
        assert ts.avg_seconds == 600.0
        assert ts.min_seconds == 300.0
        assert ts.max_seconds == 900.0
        assert ts.elapsed == timedelta(seconds=1200)
        assert ts.eta_seconds == 300.0

    def test_eta_none(self) -> None:
        ts = TimingStats(
            avg_seconds=600.0,
            min_seconds=600.0,
            max_seconds=600.0,
            elapsed=timedelta(seconds=600),
            eta_seconds=None,
        )
        assert ts.eta_seconds is None


# ── FailedItem / RetryingItem ────────────────────────────────────


class TestItemModels:
    def test_failed_item(self) -> None:
        fi = FailedItem(item="PINS-2025Q4", error="YAML parse error", attempt=2)
        assert fi.item == "PINS-2025Q4"
        assert fi.error == "YAML parse error"
        assert fi.attempt == 2

    def test_retrying_item(self) -> None:
        ri = RetryingItem(item="PINS-2025Q4", attempt=2, max_retries=5)
        assert ri.item == "PINS-2025Q4"
        assert ri.attempt == 2
        assert ri.max_retries == 5


# ── compute_progress ─────────────────────────────────────────────


class TestComputeProgress:
    def test_empty(self) -> None:
        counts = compute_progress([], total=10)
        assert counts.total == 10
        assert counts.pending == 10
        assert counts.completed == 0

    def test_all_completed(self) -> None:
        statuses = [_make_status(state="completed") for _ in range(5)]
        counts = compute_progress(statuses, total=5)
        assert counts.completed == 5
        assert counts.pending == 0
        assert counts.total == 5

    def test_mixed_states(self) -> None:
        statuses = [
            _make_status(state="completed"),
            _make_status(state="completed"),
            _make_status(state="running"),
            _make_status(state="failed", error="timeout"),
            _make_status(state="cached"),
        ]
        counts = compute_progress(statuses, total=8)
        assert counts.completed == 2
        assert counts.running == 1
        assert counts.failed == 1
        assert counts.cached == 1
        assert counts.pending == 3  # 8 - 5 scanned
        assert counts.total == 8

    def test_retrying_counted(self) -> None:
        statuses = [
            _make_status(state="running", attempt=2),
            _make_status(state="running", attempt=1),
        ]
        counts = compute_progress(statuses, total=2)
        assert counts.running == 2
        assert counts.retrying == 1

    def test_total_none_derives_from_scanned(self) -> None:
        statuses = [_make_status(state="completed") for _ in range(3)]
        counts = compute_progress(statuses, total=None)
        assert counts.total == 3
        assert counts.pending == 0


# ── compute_timing ───────────────────────────────────────────────


class TestComputeTiming:
    def test_no_completed_items(self) -> None:
        statuses = [_make_status(state="running", completed_at=None)]
        result = compute_timing(statuses)
        assert result is None

    def test_single_completed(self) -> None:
        statuses = [
            _make_status(
                state="completed",
                started_at="2026-04-04T01:00:00",
                completed_at="2026-04-04T01:10:00",
            ),
        ]
        result = compute_timing(statuses)
        assert result is not None
        assert result.avg_seconds == pytest.approx(600.0)
        assert result.min_seconds == pytest.approx(600.0)
        assert result.max_seconds == pytest.approx(600.0)

    def test_multiple_completed(self) -> None:
        statuses = [
            _make_status(
                state="completed",
                started_at="2026-04-04T01:00:00",
                completed_at="2026-04-04T01:05:00",  # 5 min = 300s
            ),
            _make_status(
                state="completed",
                started_at="2026-04-04T01:00:00",
                completed_at="2026-04-04T01:15:00",  # 15 min = 900s
            ),
            _make_status(state="running", completed_at=None),  # ignored
        ]
        result = compute_timing(statuses)
        assert result is not None
        assert result.avg_seconds == pytest.approx(600.0)
        assert result.min_seconds == pytest.approx(300.0)
        assert result.max_seconds == pytest.approx(900.0)

    def test_missing_timestamps_skipped(self) -> None:
        statuses = [
            _make_status(state="completed", started_at="", completed_at="2026-04-04T01:10:00"),
            _make_status(
                state="completed",
                started_at="2026-04-04T01:00:00",
                completed_at="2026-04-04T01:10:00",
            ),
        ]
        result = compute_timing(statuses)
        assert result is not None
        assert result.avg_seconds == pytest.approx(600.0)


# ── detect_variants ──────────────────────────────────────────────


class TestDetectVariants:
    def test_finds_variant_dirs(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4")
        _write_item(v1, "MSFT-2025Q4")

        v2 = run_dir / "pi-cli-glm5"
        _write_item(v2, "AAPL-2025Q4")

        variants = detect_variants(run_dir)
        names = {v.name for v in variants}
        assert names == {"pi-cli-deepseek", "pi-cli-glm5"}

    def test_ignores_non_variant_dirs(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        # A directory without .state/ subdirs is not a variant
        (run_dir / "logs").mkdir(parents=True)
        (run_dir / "logs" / "output.log").write_text("log data")

        # But one real variant
        _write_item(run_dir / "pi-cli-deepseek", "AAPL-2025Q4")

        variants = detect_variants(run_dir)
        assert len(variants) == 1
        assert variants[0].name == "pi-cli-deepseek"

    def test_empty_run_dir(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "empty-run"
        run_dir.mkdir()
        variants = detect_variants(run_dir)
        assert variants == []


# ── scan_variant_states ──────────────────────────────────────────


class TestScanVariantStates:
    def test_reads_status_files(self, tmp_path: Path) -> None:
        variant_handle = tmp_path / "pi-cli-deepseek"
        _write_item(variant_handle, "AAPL-2025Q4", state="completed")
        _write_item(variant_handle, "MSFT-2025Q4", state="running", completed_at=None)

        state_root = tmp_path / ".state" / "tasks" / "pi-cli-deepseek"
        statuses = scan_variant_states(state_root)
        assert len(statuses) == 2
        states = {s.state for s in statuses}
        assert states == {"completed", "running"}

    def test_skips_items_without_state(self, tmp_path: Path) -> None:
        legacy_variant_dir = tmp_path / "pi-cli-deepseek"
        _write_item(legacy_variant_dir, "AAPL-2025Q4", state="completed")
        # An item dir under the state root without a status.yaml.
        state_root = tmp_path / ".state" / "tasks" / "pi-cli-deepseek"
        (state_root / "GOOG-2025Q4").mkdir(parents=True)

        statuses = scan_variant_states(state_root)
        assert len(statuses) == 1


# ── check_completion ─────────────────────────────────────────────


class TestCheckCompletion:
    def _make_run_status(
        self, completed: int, failed: int, running: int, pending: int
    ) -> RunStatus:
        total = completed + failed + running + pending
        return RunStatus(
            run_dir=Path("/tmp/run"),
            started_at=None,
            elapsed=None,
            is_active=running > 0,
            variants=[],
            totals=ProgressCounts(
                total=total,
                completed=completed,
                failed=failed,
                running=running,
                pending=pending,
            ),
            system=None,
        )

    def test_completed_all_done(self) -> None:
        status = self._make_run_status(completed=10, failed=0, running=0, pending=0)
        result = check_completion(status, "completed")
        assert result.passed is True
        assert result.exit_code == 0

    def test_completed_has_failures(self) -> None:
        status = self._make_run_status(completed=8, failed=2, running=0, pending=0)
        result = check_completion(status, "completed")
        assert result.passed is False
        assert result.exit_code == 1

    def test_completed_still_running(self) -> None:
        status = self._make_run_status(completed=5, failed=0, running=3, pending=2)
        result = check_completion(status, "completed")
        assert result.passed is False
        assert result.exit_code == 2

    def test_no_failures_clean(self) -> None:
        status = self._make_run_status(completed=10, failed=0, running=0, pending=0)
        result = check_completion(status, "no-failures")
        assert result.passed is True
        assert result.exit_code == 0

    def test_no_failures_has_failures(self) -> None:
        status = self._make_run_status(completed=8, failed=2, running=0, pending=0)
        result = check_completion(status, "no-failures")
        assert result.passed is False
        assert result.exit_code == 1

    @pytest.mark.parametrize("condition", ["completed", "no-failures"])
    def test_process_failure_fails_even_without_item_failures(self, condition: str) -> None:
        status = self._make_run_status(completed=0, failed=0, running=0, pending=0)
        status = status.model_copy(
            update={
                "process_execution_state": "failed",
                "process_error": "intake: RuntimeError: source attestation mismatch",
            }
        )

        result = check_completion(status, condition)

        assert result.passed is False
        assert result.exit_code == 1
        assert "source attestation mismatch" in result.reason

    def test_active_resume_does_not_reuse_a_carried_terminal_verdict(self) -> None:
        status = self._make_run_status(completed=0, failed=0, running=0, pending=0)
        status = status.model_copy(
            update={
                "is_active": True,
                "orchestrator_alive": True,
                "process_execution_state": "failed",
                "process_error": "prior attempt failed",
            }
        )

        result = check_completion(status, "completed")

        assert result.passed is False
        assert result.exit_code == 2
        assert result.reason == "Run still in progress"

    def test_unknown_condition_raises(self) -> None:
        clean = self._make_run_status(completed=10, failed=0, running=0, pending=0)
        failed = clean.model_copy(update={"process_execution_state": "failed"})
        for status in (clean, failed):
            with pytest.raises(ValueError, match="Unknown check condition"):
                check_completion(status, "bogus")


# ── scan_run_status (integration) ────────────────────────────────


class TestScanRunStatus:
    def test_full_scan(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4", state="completed")
        _write_item(v1, "MSFT-2025Q4", state="completed")
        _write_item(
            v1, "GOOG-2025Q4", state="failed", error="timeout", completed_at="2026-04-04T01:10:00"
        )

        v2 = run_dir / "pi-cli-glm5"
        _write_item(v2, "AAPL-2025Q4", state="running", completed_at=None)

        result = scan_run_status(run_dir, include_system=False)
        assert len(result.variants) == 2
        assert result.totals.completed == 2
        assert result.totals.failed == 1
        assert result.totals.running == 1
        assert result.is_active is True

    def test_variant_filter(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4", state="completed")

        v2 = run_dir / "pi-cli-glm5"
        _write_item(v2, "AAPL-2025Q4", state="running", completed_at=None)

        result = scan_run_status(run_dir, variant="pi-cli-deepseek", include_system=False)
        assert len(result.variants) == 1
        assert result.variants[0].variant == "pi-cli-deepseek"
        assert result.totals.completed == 1
        assert result.totals.running == 0

    def test_failed_items_populated(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(
            v1,
            "PINS-2025Q4",
            state="failed",
            error="YAML parse error",
            completed_at="2026-04-04T01:10:00",
        )

        result = scan_run_status(run_dir, include_system=False)
        assert len(result.variants[0].failed_items) == 1
        assert result.variants[0].failed_items[0].item == "PINS-2025Q4"
        assert result.variants[0].failed_items[0].error == "YAML parse error"

    def test_is_active_true_when_orchestrator_lease_is_fresh(self, tmp_path: Path) -> None:
        """Status must report RUNNING when no items are running but the
        orchestrator lease heartbeat is fresh — the gap between fan-out
        completions and the next step's start.
        """

        run_dir = tmp_path / "my-run"
        # All items completed (no item-level activity)
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4", state="completed")
        _write_item(v1, "MSFT-2025Q4", state="completed")

        # Drop a fresh orchestrator lease at run_dir/.state/
        lease_dir = run_dir / ".state"
        lease_dir.mkdir(parents=True, exist_ok=True)
        fresh = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
        (lease_dir / "orchestrator-lease.yaml").write_text(
            f"owner_type: local\nowner_host: test\nowner_pid: 12345\n"
            f"owner_token: tok\nstarted_at: '{fresh}'\n"
            f"last_heartbeat_at: '{fresh}'\ncommand_summary: test\n"
        )

        result = scan_run_status(run_dir, include_system=False)
        assert result.is_active is True

    def test_is_active_false_when_lease_is_stale(self, tmp_path: Path) -> None:
        """A stale lease heartbeat must NOT keep status in RUNNING."""
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4", state="completed")

        lease_dir = run_dir / ".state"
        lease_dir.mkdir(parents=True, exist_ok=True)
        # Heartbeat from 1970 — well past the 120 s staleness threshold.
        (lease_dir / "orchestrator-lease.yaml").write_text(
            "owner_type: local\nowner_host: test\nowner_pid: 12345\n"
            "owner_token: tok\nstarted_at: '1970-01-01T00:00:00'\n"
            "last_heartbeat_at: '1970-01-01T00:00:00'\ncommand_summary: test\n"
        )

        result = scan_run_status(run_dir, include_system=False)
        assert result.is_active is False

    def test_is_active_false_when_same_host_lease_pid_is_dead(self, tmp_path: Path) -> None:
        """A fresh same-host lease from a dead PID must not keep status RUNNING."""

        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4", state="completed")

        lease_dir = run_dir / ".state"
        lease_dir.mkdir(parents=True, exist_ok=True)
        fresh = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
        (lease_dir / "orchestrator-lease.yaml").write_text(
            f"owner_type: local\nowner_host: {os.uname().nodename}\n"
            "owner_pid: 2147483647\n"
            f"owner_token: tok\nstarted_at: '{fresh}'\n"
            f"last_heartbeat_at: '{fresh}'\ncommand_summary: test\n"
        )

        result = scan_run_status(run_dir, include_system=False)
        assert result.is_active is False

    def test_retrying_items_populated(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "PINS-2025Q4", state="running", attempt=3, completed_at=None)

        result = scan_run_status(run_dir, include_system=False)
        assert len(result.variants[0].retrying_items) == 1
        assert result.variants[0].retrying_items[0].item == "PINS-2025Q4"
        assert result.variants[0].retrying_items[0].attempt == 3

    def test_completed_run_not_active(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4", state="completed")
        _write_item(v1, "MSFT-2025Q4", state="completed")

        result = scan_run_status(run_dir, include_system=False)
        assert result.is_active is False


# ── wait_for_completion ──────────────────────────────────────────


class TestWaitForCompletion:
    def test_live_resume_outlasts_a_carried_terminal_projection(self, tmp_path: Path) -> None:
        active = RunStatus(
            run_dir=tmp_path,
            is_active=True,
            orchestrator_alive=True,
            process_execution_state="failed",
            process_error="prior attempt failed",
            totals=ProgressCounts(),
        )
        terminal = active.model_copy(
            update={
                "is_active": False,
                "orchestrator_alive": False,
                "process_execution_state": "completed",
                "process_error": None,
            }
        )

        with patch(
            "metaproc.engine.run_status.scan_run_status",
            side_effect=[active, terminal],
        ) as scan:
            status, exit_code = wait_for_completion(
                tmp_path,
                interval=0,
                include_system=False,
            )

        assert scan.call_count == 2
        assert status is terminal
        assert exit_code == 0

    def test_already_terminal(self, tmp_path: Path) -> None:
        """If all items are already done, returns immediately."""
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4", state="completed")

        status, exit_code = wait_for_completion(run_dir, interval=0.1, include_system=False)
        assert exit_code == 0
        assert status.totals.completed == 1

    def test_terminal_with_failures(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4", state="completed")
        _write_item(
            v1, "MSFT-2025Q4", state="failed", error="timeout", completed_at="2026-04-04T01:10:00"
        )

        status, exit_code = wait_for_completion(run_dir, interval=0.1, include_system=False)
        assert exit_code == 1
        assert status.totals.failed == 1

    def test_terminal_process_failure_without_items(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        state_dir = run_dir / STATE_DIR
        state_dir.mkdir(parents=True)
        (state_dir / "process-status.yaml").write_text(
            "process: demo\n"
            "state: failed\n"
            "steps:\n"
            "  intake:\n"
            "    state: failed\n"
            "    error: 'RuntimeError: source attestation mismatch'\n",
            encoding="utf-8",
        )

        status, exit_code = wait_for_completion(run_dir, interval=0.1, include_system=False)

        assert exit_code == 1
        assert status.process_execution_state == "failed"
        assert status.process_error == "intake: RuntimeError: source attestation mismatch"

    def test_timeout(self, tmp_path: Path) -> None:
        """If items are still running and timeout expires, exit code 2."""
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2025Q4", state="running", completed_at=None)

        status, exit_code = wait_for_completion(
            run_dir, timeout=0.2, interval=0.1, include_system=False
        )
        assert exit_code == 2
        assert status.totals.running == 1


# ── read_items_total ─────────────────────────────────────────────


class TestReadItemsTotal:
    def test_reads_mine_format(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        run_dir.mkdir()
        (run_dir / "progress.md").write_text(
            "---\n"
            "progress:\n"
            "  process: mine\n"
            "  items:\n"
            "    - event_id: AAPL-2024Q4\n"
            "      ticker: AAPL\n"
            "      status: pending\n"
            "    - event_id: MSFT-2025Q4\n"
            "      ticker: MSFT\n"
            "      status: pending\n"
            "    - event_id: GOOG-2025Q1\n"
            "      ticker: GOOG\n"
            "      status: done\n"
            "---\n"
        )
        total = read_items_total(run_dir)
        assert total == 3

    def test_reads_from_parent(self, tmp_path: Path) -> None:
        """progress.md may be in the parent of the run dir (variant-level path)."""
        parent = tmp_path / "my-run"
        parent.mkdir()
        (parent / "progress.md").write_text(
            "---\nprogress:\n  items:\n    - ticker: AAPL\n    - ticker: MSFT\n---\n"
        )
        variant_dir = parent / "pi-cli-deepseek"
        variant_dir.mkdir()
        total = read_items_total(variant_dir)
        assert total == 2

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "no-progress"
        run_dir.mkdir()
        total = read_items_total(run_dir)
        assert total is None

    def test_scan_run_status_uses_items_total(self, tmp_path: Path) -> None:
        """When progress.md exists, pending count reflects items total."""
        run_dir = tmp_path / "my-run"
        run_dir.mkdir()
        (run_dir / "progress.md").write_text(
            "---\n"
            "progress:\n"
            "  items:\n"
            "    - event_id: AAPL-2024Q4\n"
            "    - event_id: MSFT-2025Q4\n"
            "    - event_id: GOOG-2025Q1\n"
            "    - event_id: AMZN-2025Q2\n"
            "    - event_id: META-2025Q3\n"
            "---\n"
        )
        v1 = run_dir / "pi-cli-deepseek"
        _write_item(v1, "AAPL-2024Q4", state="completed")
        _write_item(v1, "MSFT-2025Q4", state="completed")

        result = scan_run_status(run_dir, include_system=False)
        # Items file has 5 items, 2 completed → 3 pending
        assert result.variants[0].counts.total == 5
        assert result.variants[0].counts.completed == 2
        assert result.variants[0].counts.pending == 3


# ── _measure_subprocesses ────────────────────────────────────────


class TestMeasureSubprocesses:
    def test_parses_ps_output(self) -> None:
        fake_ps = (
            "  RSS COMM\n"
            "12345 /usr/bin/pi-cli --model deepseek\n"
            " 8000 /usr/local/bin/claude --agent\n"
            " 5000 /usr/bin/python3\n"
            " 3000 /usr/bin/gemini-cli run\n"
        )
        with patch("metaproc.engine.run_status.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = fake_ps
            count, rss_bytes = _measure_subprocesses()

        assert count == 3  # pi-cli, claude, gemini-cli
        assert rss_bytes == (12345 + 8000 + 3000) * 1024

    def test_returns_zero_on_failure(self) -> None:
        with patch("metaproc.engine.run_status.subprocess.run", side_effect=OSError("no ps")):
            count, rss_bytes = _measure_subprocesses()
        assert count == 0
        assert rss_bytes == 0


# ── Per-step status (Phase 2.1) ──────────────────────────────────


def _agent_step(step_id: str, runbook: Path, *, needs: list[str] | None = None) -> ResolvedStep:
    return ResolvedStep(
        step_id=step_id,
        mode="agent",
        adapter=ResolvedAdapter(type="test", config={}),
        prompt_paths=[str(runbook)],
        outputs={"out": IOSpec(path=f"runs/demo/{step_id}.md")},
        needs=needs or [],
    )


class TestRunStatusSteps:
    """When a plan is passed, scan_run_status populates a steps[] array
    with one StepStatusEntry per plan step."""

    def test_no_plan_means_no_steps(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        result = scan_run_status(run_dir, include_system=False)
        assert result.steps == []
        assert result.process_state is None

    def test_plan_populates_one_entry_per_step(self, tmp_path: Path) -> None:
        runbook_a = tmp_path / "a.md"
        runbook_a.write_text("v1")
        runbook_b = tmp_path / "b.md"
        runbook_b.write_text("v1")
        plan = Plan(
            process="demo",
            steps=[
                _agent_step("a", runbook_a),
                _agent_step("b", runbook_b, needs=["a"]),
            ],
        )
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        result = scan_run_status(run_dir, include_system=False, plan=plan)
        assert [entry.step_id for entry in result.steps] == ["a", "b"]
        # No state on disk → both missing → process is current (no non-current steps).
        assert all(e.state == StepState.missing for e in result.steps)
        assert result.process_state == "current"

    def test_failed_process_status_annotates_missing_step(self, tmp_path: Path) -> None:
        runbook = tmp_path / "intake.md"
        runbook.write_text("v1")
        plan = Plan(process="demo", steps=[_agent_step("intake", runbook)])
        run_dir = tmp_path / "run"
        state_dir = run_dir / STATE_DIR
        state_dir.mkdir(parents=True)
        (state_dir / "process-status.yaml").write_text(
            "process: demo\n"
            "state: failed\n"
            "steps:\n"
            "  intake:\n"
            "    state: failed\n"
            "    error: 'RuntimeError: source attestation mismatch'\n",
            encoding="utf-8",
        )

        result = scan_run_status(run_dir, include_system=False, plan=plan)

        assert result.process_execution_state == "failed"
        assert result.process_error == "intake: RuntimeError: source attestation mismatch"
        assert result.steps[0].state == StepState.missing
        assert result.steps[0].reason == (
            "last execution failed: RuntimeError: source attestation mismatch"
        )

    def test_completed_partial_run_does_not_promote_a_carried_failure(self, tmp_path: Path) -> None:
        prior_runbook = tmp_path / "prior.md"
        prior_runbook.write_text("v1")
        active_runbook = tmp_path / "active.md"
        active_runbook.write_text("v1")
        plan = Plan(
            process="demo",
            steps=[
                _agent_step("prior", prior_runbook),
                _agent_step("active", active_runbook),
            ],
        )
        run_dir = tmp_path / "run"
        state_dir = run_dir / STATE_DIR
        state_dir.mkdir(parents=True)
        (state_dir / "process-status.yaml").write_text(
            "process: demo\n"
            "state: completed\n"
            "steps:\n"
            "  prior:\n"
            "    state: failed\n"
            "    error: prior failure\n"
            "  active:\n"
            "    state: completed\n",
            encoding="utf-8",
        )

        result = scan_run_status(run_dir, include_system=False, plan=plan)

        assert result.process_execution_state == "completed"
        assert result.process_error is None
        assert result.steps[0].reason == "last execution failed: prior failure"

    def test_process_state_stale_when_any_step_stale(self, tmp_path: Path) -> None:

        runbook_a = tmp_path / "a.md"
        runbook_a.write_text("v1")
        runbook_b = tmp_path / "b.md"
        runbook_b.write_text("v1")
        step_a = _agent_step("a", runbook_a)
        step_b = _agent_step("b", runbook_b, needs=["a"])
        plan = Plan(process="demo", steps=[step_a, step_b])

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        # Mark step a as completed with a stale fingerprint (mirror points to
        # an old hash). Step b is missing.
        sa = run_dir / STATE_DIR / TASKS_SUBDIR / "a"
        sa.mkdir(parents=True, exist_ok=True)
        write_status_at(
            sa,
            StatusRecord(
                run_id="demo",
                step_id="a",
                item={"step": "a"},
                state="completed",
                started_at="2026-05-20T00:00:00",
                completed_at="2026-05-20T00:01:00",
            ),
        )
        (run_dir / STATE_DIR / "process-status.yaml").write_text(
            "process: demo\n"
            "started_at: '2026-05-20T00:00:00'\n"
            "steps:\n"
            "  a:\n"
            "    state: completed\n"
            "    recorded_step_hash: aaaaaaaaaaaaaaaa\n"
            "state: completed\n"
        )

        result = scan_run_status(run_dir, include_system=False, plan=plan)
        states = {e.step_id: e.state for e in result.steps}
        assert states == {"a": StepState.stale, "b": StepState.missing}
        assert result.process_state == "stale"

        # The stale entry carries both hashes plus a reason annotation.
        stale_entry = next(e for e in result.steps if e.step_id == "a")
        assert stale_entry.recorded_hash == "aaaaaaaaaaaaaaaa"
        assert stale_entry.current_hash == fingerprint_step(step_a)
        assert stale_entry.reason is not None
        assert "definition changed" in stale_entry.reason

    def test_missing_runbook_leaves_current_hash_none(self, tmp_path: Path) -> None:
        """A step whose referenced runbook disappeared post-completion: the
        recorded hash is still readable, but current_hash falls back to None
        rather than crashing the whole status scan."""
        runbook = tmp_path / "vanishing.md"
        runbook.write_text("body")
        step = _agent_step("a", runbook)
        plan = Plan(process="demo", steps=[step])

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        runbook.unlink()  # operator deleted the runbook
        result = scan_run_status(run_dir, include_system=False, plan=plan)
        entry = result.steps[0]
        assert entry.step_id == "a"
        assert entry.current_hash is None
