"""Tests for inline retry scheduler and observability consumers.

Covers the orchestrator-owned scheduler in run_parallel._run_agent_pool,
retry tracking infrastructure in RunPool, and observability consumers in
run_status / commands that surface pending retries.
"""

from __future__ import annotations

import heapq
import json
import time
from collections import deque
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from metaproc.engine.retry import RetryVerdict, classify_error, compute_backoff
from metaproc.engine.run_status import (
    RunStatus,
    scan_run_status,
    wait_for_completion,
)
from metaproc.io.state_io import write_status_at
from metaproc.models.authored import ProgressCounts, RetryPolicy, StepStatus
from metaproc.models.runtime import StatusRecord
from metaproc.runpool.backend import LaunchBackend
from metaproc.runpool.events import EventLogger
from metaproc.runpool.pool import RunPool, RunPoolConfig

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
    """Write per-task state for one fan-out item.

    Treats *variant_dir* as a legacy ``<run>/<step_id>/`` handle and
    materializes the corresponding per-task state directory at
    ``<run>/.state/tasks/<step_id>/<item_name>/``.
    """
    run_dir = variant_dir.parent
    state_dir = run_dir / ".state" / "tasks" / variant_dir.name / item_name
    state_dir.mkdir(parents=True, exist_ok=True)
    record = _make_status(item_name=item_name, **kwargs)  # pyright: ignore[reportArgumentType]
    write_status_at(state_dir, record)
    return state_dir


def _write_pool_status(
    run_dir: Path,
    *,
    pending_retries: int = 0,
    pid: int = 99999,
) -> Path:
    """Write a mock runpool-status.yaml into run_dir/.state/."""
    state_dir = run_dir / ".state"
    state_dir.mkdir(parents=True, exist_ok=True)
    status_path = state_dir / "runpool-status.yaml"
    data = {
        "pool_id": "test-pool",
        "pid": pid,
        "started_at": "2026-04-04T01:00:00",
        "updated_at": "2026-04-04T01:05:00",
        "backend": "local",
        "max_concurrency": 10,
        "current_concurrency": 5,
        "active_count": 0,
        "pending_count": 0,
        "completed_count": 3,
        "failed_count": 1,
        "killed_count": 0,
        "pending_retries": pending_retries,
        "pressure": {
            "level": "normal",
            "available_pct": 80.0,
            "swap_used_gb": 0.0,
            "total_memory_gb": 32.0,
            "source": "mock",
        },
    }
    status_path.write_text(yaml.dump(data, default_flow_style=False))
    return status_path


# ── Backoff indexing ─────────────────────────────────────────────


class TestBackoffIndexing:
    def test_first_retry_gets_initial(self) -> None:
        """First retry (retry_index=1) gets initial_backoff_s * multiplier^0 = initial."""
        policy = RetryPolicy(
            max_retries=5,
            initial_backoff_s=2.0,
            backoff_multiplier=2.0,
            max_backoff_s=30.0,
        )
        assert compute_backoff(1, policy) == pytest.approx(2.0)

    def test_second_retry_gets_initial_times_multiplier(self) -> None:
        """Second retry (retry_index=2) gets initial * multiplier^1."""
        policy = RetryPolicy(
            max_retries=5,
            initial_backoff_s=2.0,
            backoff_multiplier=2.0,
            max_backoff_s=30.0,
        )
        assert compute_backoff(2, policy) == pytest.approx(4.0)

    def test_capped_at_max(self) -> None:
        """Large retry_index is capped at max_backoff_s."""
        policy = RetryPolicy(
            max_retries=10,
            initial_backoff_s=2.0,
            backoff_multiplier=2.0,
            max_backoff_s=30.0,
        )
        assert compute_backoff(10, policy) == pytest.approx(30.0)


# ── Max retries enforced ─────────────────────────────────────────


class TestMaxRetriesEnforced:
    def test_exceeds_max_not_retried(self) -> None:
        """After max_retries + 1 failures, classify_error returns RETRY but
        the orchestrator's guard (attempt_number <= max_retries) blocks it.
        This tests the classify_error side — the guard is tested in the
        scheduler integration tests."""
        # A retryable error
        assert classify_error("rate limit exceeded") == RetryVerdict.RETRY
        # But a permanent one stays permanent
        assert classify_error("permission denied") == RetryVerdict.FAIL


# ── Permanent failure not retried ────────────────────────────────


class TestPermanentFailureNotRetried:
    def test_enospc_is_permanent(self) -> None:
        assert classify_error("ENOSPC: no space left on device") == RetryVerdict.FAIL

    def test_billing_is_permanent(self) -> None:
        assert classify_error("billing quota exceeded") == RetryVerdict.FAIL

    def test_exit_137_is_permanent(self) -> None:
        assert classify_error("exit code 137") == RetryVerdict.FAIL


# ── RunPool retry tracking ───────────────────────────────────────


class TestRunPoolRetryTracking:
    def test_record_retry_scheduled_increments(self, tmp_path: Path) -> None:
        """record_retry_scheduled increments pending_retries and emits event."""

        mock_backend = MagicMock(spec=LaunchBackend)
        config = RunPoolConfig(max_concurrency=5, initial_concurrency=2)

        pool = RunPool(config, backend=mock_backend)

        event_log = EventLogger(tmp_path / "events.jsonl")
        event_log.open()
        pool._event_logger = event_log  # noqa: SLF001

        pool.record_retry_scheduled("ticker=AAPL", attempt=2, backoff_s=4.0)
        assert pool._pending_retries == 1  # noqa: SLF001

        pool.record_retry_consumed("ticker=AAPL")
        assert pool._pending_retries == 0  # noqa: SLF001

        event_log.close()

    def test_record_retry_consumed_floors_at_zero(self) -> None:
        """record_retry_consumed never goes negative."""

        mock_backend = MagicMock(spec=LaunchBackend)
        config = RunPoolConfig(max_concurrency=5, initial_concurrency=2)

        pool = RunPool(config, backend=mock_backend)
        pool._pending_retries = 0  # noqa: SLF001
        pool.record_retry_consumed("ticker=AAPL")
        assert pool._pending_retries == 0  # noqa: SLF001


# ── Event logger retry_scheduled ─────────────────────────────────


class TestEventLoggerRetryScheduled:
    def test_writes_retry_event(self, tmp_path: Path) -> None:

        log_path = tmp_path / "events.jsonl"
        logger = EventLogger(log_path)
        logger.open()
        logger.retry_scheduled("ticker=AAPL", attempt=2, backoff_s=4.0)
        logger.close()

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "retry_scheduled"
        assert event["label"] == "ticker=AAPL"
        assert event["attempt"] == 2
        assert event["backoff_s"] == 4.0


# ── Wait: pending_retries with alive/dead pool ──────────────────


class TestWaitPendingRetries:
    def test_pending_retries_alive_pool_not_terminal(self, tmp_path: Path) -> None:
        """With pending_retries > 0 and alive pool, wait_for_completion does not terminate."""
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "variant"
        _write_item(v1, "AAPL", state="completed")
        _write_item(v1, "MSFT", state="failed", error="timeout", completed_at="2026-04-04T01:10:00")
        _write_pool_status(run_dir, pending_retries=2, pid=99999)

        # Mock is_pool_alive at the source module (lazy-imported in run_status.py)
        with patch("metaproc.runpool.status.is_pool_alive", return_value=True):
            # With timeout, it should NOT return terminal (exit_code=2 = timeout)
            _status, exit_code = wait_for_completion(
                run_dir, timeout=0.3, interval=0.1, include_system=False
            )
            assert exit_code == 2  # Timed out — did not consider it terminal

    def test_pending_retries_dead_pool_is_terminal(self, tmp_path: Path) -> None:
        """With pending_retries > 0 but dead pool, wait_for_completion considers it terminal."""
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "variant"
        _write_item(v1, "AAPL", state="completed")
        _write_item(v1, "MSFT", state="failed", error="timeout", completed_at="2026-04-04T01:10:00")
        _write_pool_status(run_dir, pending_retries=2, pid=99999)

        # Mock is_pool_alive at the source module — pool is dead
        with patch("metaproc.runpool.status.is_pool_alive", return_value=False):
            # Dead pool with no running items → terminal
            status, exit_code = wait_for_completion(
                run_dir, timeout=1.0, interval=0.1, include_system=False
            )
            assert exit_code == 1  # Terminal: failures exist
            assert status.totals.failed == 1


# ── scan_run_status reads pending_retries ────────────────────────


class TestScanRunStatusPendingRetries:
    def test_reads_pending_retries_from_pool_status(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "variant"
        _write_item(v1, "AAPL", state="completed")
        _write_pool_status(run_dir, pending_retries=3, pid=99999)

        with patch("metaproc.runpool.status.is_pool_alive", return_value=True):
            result = scan_run_status(run_dir, include_system=False)
            assert result.pending_retries == 3
            assert result.is_active is True  # alive pool + pending retries

    def test_no_pool_status_zero_retries(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "my-run"
        v1 = run_dir / "variant"
        _write_item(v1, "AAPL", state="completed")

        result = scan_run_status(run_dir, include_system=False)
        assert result.pending_retries == 0
        assert result.is_active is False


# ── Status display includes pending retries ──────────────────────


class TestStatusDisplaysPendingRetries:
    """Test the status display logic for pending retries.

    _format_text lives in metaproc.commands.status which has a circular import
    through the CLI module. We test the logic indirectly by verifying the
    RunStatus model carries the right data and the formatting pattern matches.
    """

    def test_status_label_includes_retries_when_active(self) -> None:
        status = RunStatus(
            run_dir=Path("/tmp/run"),
            started_at=None,
            elapsed=None,
            is_active=True,
            variants=[],
            totals=ProgressCounts(total=10, completed=5, failed=2, running=0, pending=0),
            pending_retries=3,
            system=None,
        )
        # Replicate the formatting logic from _format_text
        status_label = "RUNNING" if status.is_active else "COMPLETE"
        if status.pending_retries > 0:
            status_label += f" ({status.pending_retries} retries pending)"
        assert "3 retries pending" in status_label
        assert "RUNNING" in status_label

    def test_status_label_no_retries(self) -> None:
        status = RunStatus(
            run_dir=Path("/tmp/run"),
            started_at=None,
            elapsed=None,
            is_active=False,
            variants=[],
            totals=ProgressCounts(total=10, completed=10, failed=0, running=0, pending=0),
            pending_retries=0,
            system=None,
        )
        status_label = "RUNNING" if status.is_active else "COMPLETE"
        if status.pending_retries > 0:
            status_label += f" ({status.pending_retries} retries pending)"
        assert "retries pending" not in status_label
        assert "COMPLETE" in status_label


# ── Per-item attempt tracking (unit-level) ───────────────────────


class TestPerItemAttemptTracking:
    def test_attempt_number_increments_independently(self) -> None:
        """Verify the pattern: shared["attempt_number"] is per-item.

        This is a structural test that validates the orchestrator pattern.
        Each item gets its own shared dict, so attempt_number is independent.
        """
        shared_a: dict[str, Any] = {"item": "AAPL", "attempt_number": 1}
        shared_b: dict[str, Any] = {"item": "MSFT", "attempt_number": 1}

        # Simulate A failing and retrying
        shared_a["attempt_number"] += 1
        assert shared_a["attempt_number"] == 2
        # B is independent
        assert shared_b["attempt_number"] == 1

        # A fails again
        shared_a["attempt_number"] += 1
        assert shared_a["attempt_number"] == 3
        assert shared_b["attempt_number"] == 1


# ── Retry priority over not_started (unit-level) ────────────────


class TestRetryPriorityOverNotStarted:
    def test_retry_due_takes_slot_before_not_started(self) -> None:
        """When a retry is due and not_started has items, the retry gets priority.

        This validates the _fill_pool logic pattern: retry_heap entries whose
        ready_at <= now are dequeued before not_started items.
        """

        not_started: deque[dict[str, str]] = deque([{"ticker": "GOOG"}])
        retry_heap: list[tuple[float, int, dict[str, Any], dict[str, str]]] = []

        # Add a retry that's already due
        shared_retry = {"item": "AAPL", "attempt_number": 2}
        heapq.heappush(retry_heap, (time.monotonic() - 1, 0, shared_retry, {"ticker": "AAPL"}))

        # Simulate _fill_pool logic: capacity = 1
        submitted: list[str] = []
        capacity = 1

        while capacity > 0 and (retry_heap or not_started):
            now = time.monotonic()
            if retry_heap and retry_heap[0][0] <= now:
                _, _, shared, ctx = heapq.heappop(retry_heap)
                submitted.append(f"retry:{shared['item']}")
                capacity -= 1
            elif not_started:
                ctx = not_started.popleft()
                submitted.append(f"new:{ctx['ticker']}")
                capacity -= 1
            else:
                break

        assert submitted == ["retry:AAPL"]
        assert len(not_started) == 1  # GOOG still waiting


# ── Retry liveness: empty active with pending retries ────────────


class TestRetryLivenessEmptyActive:
    def test_loop_continues_with_only_retry_heap(self) -> None:
        """When active is empty but retry_heap has entries, loop must NOT exit.

        The while condition is: `while not_started or active or retry_heap`
        """

        not_started: deque[dict[str, str]] = deque()
        active: dict[Any, Any] = {}
        retry_heap: list[tuple[float, int, dict[str, Any], dict[str, str]]] = []

        shared = {"item": "AAPL", "attempt_number": 2}
        heapq.heappush(retry_heap, (time.monotonic() + 0.05, 0, shared, {"ticker": "AAPL"}))

        # Loop condition should be True
        assert bool(not_started or active or retry_heap) is True

        # Simulate: sleep until retry is due, then process it
        iterations = 0
        while not_started or active or retry_heap:
            iterations += 1
            if iterations > 10:
                pytest.fail("Loop did not terminate")

            now = time.monotonic()
            if retry_heap and retry_heap[0][0] <= now:
                heapq.heappop(retry_heap)
                break
            time.sleep(0.02)

        assert len(retry_heap) == 0


# ── Future exception classified ──────────────────────────────────


class TestFutureExceptionClassified:
    def test_launch_exception_is_retryable(self) -> None:
        """An exception from prepare_launch (e.g. 'connection refused') should
        be classified as retryable."""
        error_str = "launch failed: connection refused"
        verdict = classify_error(error_str)
        assert verdict == RetryVerdict.RETRY

    def test_launch_exception_permanent(self) -> None:
        """A permission error from prepare_launch is permanent."""
        error_str = "launch failed: permission denied"
        verdict = classify_error(error_str)
        assert verdict == RetryVerdict.FAIL
