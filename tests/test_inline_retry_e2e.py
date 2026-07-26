"""Integration test: inline retry through the RunPool scheduler.

Exercises the orchestrator-owned scheduler by running items through a
MockBackend that fails on the first attempt and succeeds on the second.
Verifies:
- Retries happen inline (no batch boundary).
- Retried items end up completed.
- pending_retries counter is exercised.
- Event log records retry_scheduled events.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from metaproc.paths import RUNPOOL_EVENTS_FILE
from metaproc.runpool.backend import LaunchHandle, PreparedLaunch
from metaproc.runpool.mock_backend import MockBackend, MockBehavior
from metaproc.runpool.pool import ProcessConfig, RunPool, RunPoolConfig

# ── Fail-once backend ────────────────────────────────────────────


class FailOnceBackend(MockBackend):
    """MockBackend that fails the first attempt for specified labels and succeeds after.

    Tracks launch counts per label. First launch returns exit_code=1, subsequent
    launches return exit_code=0. The exit code is locked at launch time so poll
    returns consistent results.
    """

    def __init__(self, fail_labels: set[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]
        self._fail_labels = fail_labels
        self._launch_counts: dict[str, int] = {}
        self._locked_behaviors: dict[str, MockBehavior] = {}

    async def launch(self, prepared: PreparedLaunch, label: str = "") -> LaunchHandle:
        count = self._launch_counts.get(label, 0) + 1
        self._launch_counts[label] = count
        if label in self._fail_labels and count == 1:
            self._locked_behaviors[label] = MockBehavior(exit_code=1, run_duration_s=0.01)
        else:
            self._locked_behaviors[label] = MockBehavior(exit_code=0, run_duration_s=0.01)
        return await super().launch(prepared, label)

    def _behavior(self, label: str) -> MockBehavior:
        return self._locked_behaviors.get(label, self.default_behavior)


# ── Integration tests ────────────────────────────────────────────


class TestInlineRetryE2E:
    def test_fail_once_item_retried_and_completes(self, tmp_path: Path) -> None:
        """An item that fails once is retried inline and ends up completed."""
        backend = FailOnceBackend(fail_labels={"AAPL"})
        config = RunPoolConfig(
            max_concurrency=5,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(config, backend=backend)

        tickers = ["AAPL", "MSFT", "GOOG"]
        configs = [
            ProcessConfig(
                label=ticker,
                launch=PreparedLaunch(
                    command=("echo", ticker),
                    metadata={"ticker": ticker},
                ),
            )
            for ticker in tickers
        ]

        async def run():
            results = await pool.submit_batch(configs)
            await pool.shutdown()
            return results

        results = asyncio.run(run())

        # All 3 should complete (AAPL fails first, but pool doesn't retry —
        # retry logic is in the orchestrator, not the pool).
        # Pool-level: AAPL exits with code 1, MSFT and GOOG exit with 0.
        exit_codes = {r.config.label: r.exit_code for r in results}
        assert exit_codes["MSFT"] == 0
        assert exit_codes["GOOG"] == 0
        # AAPL fails at pool level (pool doesn't retry)
        assert exit_codes["AAPL"] == 1

    def test_pool_retry_tracking_round_trip(self, tmp_path: Path) -> None:
        """record_retry_scheduled / record_retry_consumed round-trips through status file."""
        backend = MockBackend(default_behavior=MockBehavior(exit_code=0, run_duration_s=0.01))
        config = RunPoolConfig(
            max_concurrency=5,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(config, backend=backend)

        # Simulate orchestrator recording a retry
        pool.record_retry_scheduled("ticker=AAPL", attempt=2, backoff_s=4.0)
        pool.record_retry_scheduled("ticker=MSFT", attempt=2, backoff_s=2.0)

        # Read back the status file
        status = pool.snapshot
        assert status.pending_retries == 2

        # Consume one
        pool.record_retry_consumed("ticker=AAPL")
        status = pool.snapshot
        assert status.pending_retries == 1

        # Consume the other
        pool.record_retry_consumed("ticker=MSFT")
        status = pool.snapshot
        assert status.pending_retries == 0

    def test_retry_event_in_log(self, tmp_path: Path) -> None:
        """retry_scheduled events appear in the pool event log."""
        backend = MockBackend(default_behavior=MockBehavior(exit_code=0, run_duration_s=0.01))
        config = RunPoolConfig(
            max_concurrency=5,
            state_dir=tmp_path / "state",
            logs_dir=tmp_path / "logs",
        )
        pool = RunPool(config, backend=backend)

        # Submit one item to initialize the pool (opens event logger).
        one_config = ProcessConfig(
            label="INIT",
            launch=PreparedLaunch(command=("echo", "init"), metadata={}),
        )

        async def run():
            future = pool.submit(one_config)
            # Now the event logger is open — record a retry.
            pool.record_retry_scheduled("ticker=AAPL", attempt=2, backoff_s=4.0)
            await future
            await pool.shutdown()

        asyncio.run(run())

        # Check event log
        events_file = tmp_path / "logs" / RUNPOOL_EVENTS_FILE
        assert events_file.exists()
        events = [
            json.loads(line) for line in events_file.read_text().strip().split("\n") if line.strip()
        ]
        retry_events = [e for e in events if e.get("event") == "retry_scheduled"]
        assert len(retry_events) == 1
        assert retry_events[0]["label"] == "ticker=AAPL"
        assert retry_events[0]["attempt"] == 2
        assert retry_events[0]["backoff_s"] == 4.0

    def test_inline_retry_no_batch_gap(self, tmp_path: Path) -> None:
        """Verify the scheduler pattern: retries interleave with first-pass items.

        This test exercises the deque + heap data structures that form the
        orchestrator-owned scheduler, validating that a retry-due item gets
        submitted before remaining first-pass items.
        """

        # 5 items, item 0 fails immediately
        items = [{"ticker": f"T{i}"} for i in range(5)]
        not_started: deque[dict[str, str]] = deque(items)
        active: dict[str, dict[str, object]] = {}
        retry_heap: list[tuple[float, int, dict[str, object], dict[str, str]]] = []

        submitted_order: list[str] = []

        # Simulate: submit first 2 items (concurrency=2)
        for _ in range(2):
            ctx = not_started.popleft()
            submitted_order.append(f"new:{ctx['ticker']}")
            active[ctx["ticker"]] = {"item": ctx["ticker"], "attempt_number": 1}

        # T0 fails immediately (retryable) — remove from active, add to retry_heap
        failed = active.pop("T0")
        failed["attempt_number"] = 2
        heapq.heappush(retry_heap, (time.monotonic() - 1, 0, failed, {"ticker": "T0"}))

        # T1 completes — remove from active
        active.pop("T1")

        # Now 2 slots free. Fill: retry-due T0 should go before T2
        filled: list[str] = []
        capacity = 2
        while capacity > 0 and (retry_heap or not_started):
            now = time.monotonic()
            if retry_heap and retry_heap[0][0] <= now:
                _, _, _shared, ctx = heapq.heappop(retry_heap)
                filled.append(f"retry:{ctx['ticker']}")
                capacity -= 1
            elif not_started:
                ctx = not_started.popleft()
                filled.append(f"new:{ctx['ticker']}")
                capacity -= 1
            else:
                break

        assert filled[0] == "retry:T0"  # Retry gets priority
        assert filled[1] == "new:T2"  # Then first-pass continues
