"""RunPool used directly, the way a caller outside this package would.

Everything here constructs the pool itself rather than going through ``run-parallel``.
That is the whole point: the pool has always been reachable only via the fan-out path,
so any script wanting bounded, admitted, health-tracked launches had to reimplement one.

These are the guarantees a standalone caller depends on, so they are tested from the
outside in: submit returns results, the context manager shuts the pool down even when
the body raises, and concurrency is actually bounded by the configured ceiling.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from metaproc.runpool import ProcessConfig, ProcessResult, RunPool, RunPoolConfig
from metaproc.runpool.backend import LocalBackend, PreparedLaunch


def _launch(script: str, tmp_path: Path, label: str) -> PreparedLaunch:
    return PreparedLaunch(
        command=(sys.executable, "-c", script),
        env={},
        cwd=None,
        log_path=tmp_path / f"{label}.log",
    )


def _config(tmp_path: Path, *, max_concurrency: int = 2) -> RunPoolConfig:
    return RunPoolConfig(
        max_concurrency=max_concurrency,
        initial_concurrency=max_concurrency,
        min_concurrency=1,
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
        host_admission_enabled=False,
    )


class TestSubmit:
    def test_a_submitted_process_runs_and_reports_success(self, tmp_path: Path) -> None:
        async def run() -> ProcessResult:
            async with RunPool(_config(tmp_path), backend=LocalBackend()) as pool:
                return await pool.submit(
                    ProcessConfig(
                        launch=_launch("print('ok')", tmp_path, "a"),
                        label="a",
                    )
                )

        result = asyncio.run(run())
        assert result.exit_code == 0

    def test_a_failing_process_reports_its_exit_code(self, tmp_path: Path) -> None:
        async def run() -> ProcessResult:
            async with RunPool(_config(tmp_path), backend=LocalBackend()) as pool:
                return await pool.submit(
                    ProcessConfig(
                        launch=_launch("import sys; sys.exit(3)", tmp_path, "b"),
                        label="b",
                    )
                )

        result = asyncio.run(run())
        assert result.exit_code == 3


def _tasks_still_pending() -> int:
    """Count live tasks other than the caller, from inside the running loop.

    Checking after ``asyncio.run`` returns would prove nothing, because closing the
    loop discards everything either way. The leak only shows while the loop is alive.
    """
    current = asyncio.current_task()
    return len([t for t in asyncio.all_tasks() if t is not current and not t.done()])


class TestContextManager:
    def test_exiting_the_context_leaves_no_pool_task_running(self, tmp_path: Path) -> None:
        """The pool's monitor task is the thing that leaks when shutdown is skipped."""

        async def run() -> int:
            async with RunPool(_config(tmp_path), backend=LocalBackend()) as pool:
                await pool.submit(
                    ProcessConfig(launch=_launch("print('x')", tmp_path, "c"), label="c")
                )
            return _tasks_still_pending()

        assert asyncio.run(run()) == 0

    def test_the_pool_shuts_down_when_the_body_raises(self, tmp_path: Path) -> None:
        """An unshut pool leaks its monitor task, its event log, and any host slots it
        holds, and a held slot is invisible capacity loss for every other run."""

        async def run() -> int:
            try:
                async with RunPool(_config(tmp_path), backend=LocalBackend()) as pool:
                    # Submit first: the monitor task only exists once work has been
                    # dispatched, so raising before this would leak nothing and the
                    # test would pass without exercising the exception path at all.
                    await pool.submit(
                        ProcessConfig(launch=_launch("print('y')", tmp_path, "e"), label="e")
                    )
                    raise RuntimeError("caller blew up")
            except RuntimeError:
                pass
            return _tasks_still_pending()

        assert asyncio.run(run()) == 0


class TestBoundedConcurrency:
    def test_concurrency_never_exceeds_the_configured_ceiling(self, tmp_path: Path) -> None:
        """The property a hand-rolled ThreadPoolExecutor gives up: an adaptive ceiling
        the caller does not have to size by hand."""
        peak = 0

        async def run() -> None:
            nonlocal peak
            async with RunPool(
                _config(tmp_path, max_concurrency=2), backend=LocalBackend()
            ) as pool:
                futures = [
                    pool.submit(
                        ProcessConfig(
                            launch=_launch("import time; time.sleep(0.3)", tmp_path, f"d{i}"),
                            label=f"d{i}",
                        )
                    )
                    for i in range(6)
                ]
                while any(not f.done() for f in futures):
                    peak = max(peak, pool.active_count)
                    await asyncio.sleep(0.02)
                await asyncio.gather(*futures)

        asyncio.run(run())
        assert 0 < peak <= 2
