"""Provider-free admission checks for executable leaves of a process run."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands import run_parallel, run_process
from metaproc.commands.run_process import RunExecutionContext, _leaf_slot
from metaproc.models.authored import ProcessSpec, ProcessStep, RetryPolicy
from metaproc.models.plan import FanOut, ResolvedStep
from metaproc.osutils.memory_pressure import MemoryPressure, PressureLevel
from metaproc.runpool import pool as pool_module
from metaproc.runpool.pool import ProcessConfig, ProcessResult
from metaproc.runpool.status import is_pool_alive


def test_code_only_admission_starts_controller_and_tracks_capacity(tmp_path: Path) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=4, initial_concurrency=1, run_dir=tmp_path, enable_run_pool=True
        )
        release = asyncio.Event()
        entered: list[int] = []

        async def leaf(index: int) -> None:
            async with _leaf_slot(context):
                entered.append(index)
                await release.wait()

        tasks = [asyncio.create_task(leaf(i)) for i in range(6)]
        try:
            await asyncio.sleep(0.02)
            assert len(entered) == 1
            assert context.run_pool_owner is not None
            pool = context.run_pool_owner.pool
            assert pool is not None
            assert pool.snapshot.active_code_count == 1
            pool._set_capacity(6, reason="test_live_growth")
            await asyncio.sleep(0.02)
            assert len(entered) == 6
            pool._set_capacity(1, reason="test_memory_pressure")
            waiting = asyncio.create_task(leaf(6))
            tasks.append(waiting)
            await asyncio.sleep(0.02)
            assert len(entered) == 6
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            assert pool.snapshot.active_code_count == 0
            assert pool.snapshot.pending_code_count == 0
            async with _leaf_slot(context):
                assert pool.snapshot.active_code_count == 1
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await context.aclose()

    asyncio.run(check())


def test_code_first_profile_binding_shares_permit_without_double_acquire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=4, initial_concurrency=1, run_dir=tmp_path, enable_run_pool=True
        )
        started = asyncio.Event()

        async def fake_launch(config: ProcessConfig, **_kwargs: object) -> ProcessResult:
            started.set()
            return ProcessResult(config, None, None, "fake", 0, None, 0, None, None, None)

        async def no_host_slot(_config: ProcessConfig) -> None:
            return None

        future = None
        try:
            async with _leaf_slot(context):
                assert context.run_pool_owner is not None
                original = context.run_pool_owner.pool
                pool = context.agent_pool(
                    resource_config={"estimated_process_rss_mb": 250},
                    execution_profile="small-agent",
                )
                assert pool is not None and pool is original
                assert pool.snapshot.active_code_count == 1
                assert pool.snapshot.concurrency_plan is not None
                assert pool.snapshot.concurrency_plan.estimated_process_rss_bytes == 250 * 1024**2
                monkeypatch.setattr(pool, "_launch_and_monitor", fake_launch)
                monkeypatch.setattr(pool, "_acquire_host_slot", no_host_slot)
                future = pool.submit(ProcessConfig(label="fake-agent"))
                await asyncio.sleep(0.02)
                assert not started.is_set()
            assert future is not None
            result = await asyncio.wait_for(future, timeout=1)
            assert result.exit_code == 0
            assert started.is_set()
            assert pool._semaphore.held == 0
            assert pool.snapshot.active_code_count == 0
        finally:
            await context.aclose()

    asyncio.run(check())


@pytest.mark.parametrize("aligned", [False, True])
@pytest.mark.parametrize("step_limit", [None, 2])
def test_mapped_code_uses_live_gate_and_preserves_step_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, aligned: bool, step_limit: int | None
) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=1, initial_concurrency=1, run_dir=tmp_path, enable_run_pool=True
        )
        items = [{"item": str(i)} for i in range(4)]
        monkeypatch.setattr(run_process, "_discover_chain_items", lambda **_kwargs: items)
        monkeypatch.setattr(
            run_process, "_refresh_run_plan_item_keys", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(run_process, "_item_task_is_done", lambda **_kwargs: False)
        monkeypatch.setattr(run_process, "_prepare_mapped_dispatch", lambda **_kwargs: None)
        entered: list[str] = []
        release = asyncio.Event()

        async def fake_code(**kwargs: Any) -> bool:
            entered.append(kwargs["variables"]["item"])
            await release.wait()
            return True

        monkeypatch.setattr(run_process, "_execute_code_step", fake_code)
        target = ResolvedStep(
            step_id="work",
            mode="code",
            fan_out=FanOut(over="items", bind="item", source="unused", max_concurrency=step_limit),
        )
        definition = ProcessStep(id="work", mode="code", command="true")
        common: dict[str, Any] = dict(
            spec=ProcessSpec(name="test"),
            variables={},
            process_dir=tmp_path,
            run_dir=tmp_path,
            run_id="run-test",
            max_concurrency=1,
            execution_context=context,
            out=SimpleNamespace(progress=lambda *_args: None),
        )
        task: asyncio.Task[Any]
        if aligned:
            task = asyncio.create_task(
                run_process._execute_item_aligned_chain(
                    chain=["work"],
                    step_map={"work": target},
                    step_def_map={"work": definition},
                    **common,
                )
            )
        else:
            task = asyncio.create_task(
                run_process._execute_code_fan_out_step(
                    target=target,
                    step_def=definition,
                    **common,
                )
            )
        try:
            await asyncio.sleep(0.02)
            assert len(entered) == 1
            assert context.run_pool_owner is not None and context.run_pool_owner.pool is not None
            context.run_pool_owner.pool._set_capacity(4, reason="test_live_growth")
            await asyncio.sleep(0.02)
            assert len(entered) == (step_limit or 4)
            release.set()
            await asyncio.wait_for(task, timeout=1)
            assert sorted(entered) == ["0", "1", "2", "3"]
        finally:
            release.set()
            await task
            await context.aclose()

    asyncio.run(check())


def test_cli_override_reaches_code_admission_and_reports_live_capacity(tmp_path: Path) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=1, initial_concurrency=1, run_dir=tmp_path, enable_run_pool=True
        )
        release = asyncio.Event()
        entered: list[int] = []

        async def code(index: int) -> None:
            async with _leaf_slot(context):
                entered.append(index)
                await release.wait()

        tasks = [asyncio.create_task(code(i)) for i in range(3)]
        try:
            await asyncio.sleep(0.02)
            assert context.run_pool_owner is not None and context.run_pool_owner.pool is not None
            pool = context.run_pool_owner.pool
            result = CliRunner().invoke(
                app,
                ["pool", "override", str(tmp_path), "--max-concurrency", "3", "--mode", "adaptive"],
            )
            assert result.exit_code == 0, result.output
            pool._read_overrides()
            for _ in range(12):
                pool._adjust_concurrency(PressureLevel.NORMAL)
            await asyncio.sleep(0.02)
            assert len(entered) == 3
            assert pool.snapshot.current_concurrency == 3
            assert pool.snapshot.controller is not None
            assert pool.snapshot.controller.effective_target == 3
            result = CliRunner().invoke(app, ["pool", "override", str(tmp_path), "--cap", "1"])
            assert result.exit_code == 0, result.output
            pool._read_overrides()
            pool._apply_operator_target()
            assert pool.snapshot.current_concurrency == 1
            assert pool.snapshot.active_code_count == 3
        finally:
            release.set()
            await asyncio.gather(*tasks)
            await context.aclose()

    asyncio.run(check())


def test_first_agent_policy_shrinks_code_bootstrap_before_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pool_module, "measure", lambda: MemoryPressure(25, 0, 8, PressureLevel.ELEVATED, "fixture")
    )

    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=8, initial_concurrency=4, run_dir=tmp_path, enable_run_pool=True
        )
        try:
            async with _leaf_slot(context):
                assert context.run_pool_owner is not None
                pool = context.agent_pool(
                    resource_config={"estimated_process_rss_mb": 2048}, execution_profile="large"
                )
                assert pool is not None
                assert pool.snapshot.concurrency_plan is not None
                assert (
                    pool.current_max_concurrency
                    == pool.snapshot.concurrency_plan.initial_concurrency_estimate
                    == 1
                )
                assert pool._semaphore.held == 1
        finally:
            await context.aclose()

    asyncio.run(check())


def test_shutdown_waits_for_code_holder_and_cancels_waiter(tmp_path: Path) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=1, initial_concurrency=1, run_dir=tmp_path, enable_run_pool=True
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def code() -> None:
            async with _leaf_slot(context):
                entered.set()
                await release.wait()

        holder = asyncio.create_task(code())
        await entered.wait()
        waiter = asyncio.create_task(code())
        closing = asyncio.create_task(context.aclose())
        await asyncio.sleep(0.02)
        was_pending = not closing.done()
        release.set()
        await asyncio.gather(holder, waiter, closing, return_exceptions=True)
        assert was_pending
        assert context.run_pool_owner is not None and context.run_pool_owner.pool is not None
        assert context.run_pool_owner.pool._semaphore.held == 0
        assert context.run_pool_owner.pool.snapshot.active_code_count == 0

    asyncio.run(check())


@pytest.mark.parametrize("cancel", [False, True])
def test_mapped_agent_shares_code_controller_and_owns_only_its_submissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=2, initial_concurrency=1, run_dir=tmp_path, enable_run_pool=True
        )
        started = asyncio.Event()
        release_agent = asyncio.Event()
        release_code = asyncio.Event()
        code_entered = asyncio.Event()

        async def code() -> None:
            async with _leaf_slot(context):
                code_entered.set()
                await release_code.wait()

        holder = asyncio.create_task(code())
        await code_entered.wait()
        pool = context.agent_pool(resource_config={}, execution_profile="fixture")
        assert pool is not None

        async def fake_launch(config: ProcessConfig, **_kwargs: object) -> ProcessResult:
            started.set()
            await release_agent.wait()
            return ProcessResult(config, None, None, "fake", 0, None, 0, None, None, None)

        async def no_host_slot(_config: ProcessConfig) -> None:
            return None

        monkeypatch.setattr(pool, "_launch_and_monitor", fake_launch)
        monkeypatch.setattr(pool, "_acquire_host_slot", no_host_slot)
        monkeypatch.setattr(run_parallel, "_POOL_FILL_POLL_INTERVAL_S", 0.01)
        monkeypatch.setattr(run_parallel, "_build_prepare_launch", lambda **_kwargs: None)
        monkeypatch.setattr(run_parallel, "compute_run_dir", lambda *_args: tmp_path)
        monkeypatch.setattr(
            run_parallel, "compute_task_state_dir", lambda *_args: tmp_path / "item"
        )
        monkeypatch.setattr(
            run_parallel, "_handle_success", lambda *args, **_kwargs: args[13].append((args[1], 0))
        )
        task = asyncio.create_task(
            run_parallel._run_agent_pool(
                spec=ProcessSpec(name="fixture"),
                step_def=ProcessStep(id="agent", mode="agent"),
                step="agent",
                each="ticker",
                variables={"RUN_ID": "test"},
                item_contexts=[{"ticker": "AFL"}],
                adapter_type="fixture",
                merged_config={},
                effective_outputs=None,
                effective_variant="",
                allowed_runtime=set(),
                retry_policy=RetryPolicy(max_retries=0),
                process_dir=tmp_path,
                refresh_token_fn=None,
                pool_config=pool._config,
                backend=None,
                out=MagicMock(),
                shared_pool=pool,
            )
        )
        try:
            await asyncio.sleep(0.03)
            assert not started.is_set()
            if cancel:
                pool._set_capacity(2, reason="test_overlap")
            else:
                release_code.set()
            await asyncio.wait_for(started.wait(), timeout=2)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not holder.done()
                assert not pool.shutting_down
                assert pool.snapshot.active_code_count == 1
            else:
                release_agent.set()
                assert await asyncio.wait_for(task, timeout=2) == [("AFL", 0)]
                assert not pool.shutting_down
        finally:
            release_code.set()
            release_agent.set()
            await asyncio.gather(holder, task, return_exceptions=True)
            await context.aclose()
        assert pool._semaphore.held == 0
        assert not is_pool_alive(pool.snapshot)

    asyncio.run(check())


def test_no_owner_mapped_code_keeps_shared_fixed_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=1, run_dir=tmp_path, enable_run_pool=False
        )
        entered: list[str] = []
        release = asyncio.Event()

        async def code(**kwargs: Any) -> bool:
            entered.append(kwargs["target"].step_id)
            await release.wait()
            return True

        monkeypatch.setattr(run_process, "_execute_code_step", code)

        async def item(name: str) -> bool:
            return await run_process._execute_mapped_code_item(
                spec=ProcessSpec(name="fixture"),
                step_def=ProcessStep(id=name, mode="code", command="true"),
                target=ResolvedStep(step_id=name, mode="code"),
                variables={},
                process_dir=tmp_path,
                run_dir=tmp_path,
                run_id="fixture",
                execution_context=context,
                out=MagicMock(),
            )

        tasks = [asyncio.create_task(item(name)) for name in ["mapped", "aligned"]]
        try:
            await asyncio.sleep(0.02)
            assert len(entered) == 1
        finally:
            release.set()
            assert await asyncio.wait_for(asyncio.gather(*tasks), 1) == [True, True]
            await context.aclose()
        assert entered == ["mapped", "aligned"]

    asyncio.run(check())


def test_live_override_preserves_profile_limit(tmp_path: Path) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=4, initial_concurrency=1, run_dir=tmp_path, enable_run_pool=True
        )
        pool = context.agent_pool(
            resource_config={"max_concurrency_hint": 2}, execution_profile="limited"
        )
        assert pool is not None
        try:
            result = CliRunner().invoke(
                app,
                ["pool", "override", str(tmp_path), "--max-concurrency", "6", "--mode", "adaptive"],
            )
            assert result.exit_code == 0
            pool._read_overrides()
            for _ in range(12):
                pool._adjust_concurrency(PressureLevel.NORMAL)
            assert pool.current_max_concurrency == 2
        finally:
            await context.aclose()

    asyncio.run(check())


def test_code_bootstrap_does_not_inherit_first_agent_resource_hint(tmp_path: Path) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=4, initial_concurrency=4, run_dir=tmp_path, enable_run_pool=True
        )
        try:
            target = ResolvedStep(
                step_id="code", mode="code", resources={"max_concurrency_hint": 1}
            )
            async with _leaf_slot(context, target):
                assert (
                    context.run_pool_owner is not None and context.run_pool_owner.pool is not None
                )
                assert context.run_pool_owner.pool.current_max_concurrency == 4
        finally:
            await context.aclose()

    asyncio.run(check())


@pytest.mark.parametrize("cancel", [False, True])
def test_due_retry_yields_when_sibling_holds_shared_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    async def check() -> None:
        context = RunExecutionContext.create(
            max_concurrency=1, initial_concurrency=1, run_dir=tmp_path, enable_run_pool=True
        )
        pool = context.agent_pool(resource_config={}, execution_profile="fixture")
        assert pool is not None
        first_started = asyncio.Event()
        code_entered = asyncio.Event()
        release_code = asyncio.Event()
        launches = 0
        snapshots = 0
        original_status = pool._build_status

        def bounded_status() -> Any:
            nonlocal snapshots
            snapshots += 1
            # A synchronous starvation defect must fail rather than hang pytest.
            assert snapshots < 1000, "due retry spins without yielding to sibling work"
            return original_status()

        async def fake_launch(config: ProcessConfig, **_kwargs: object) -> ProcessResult:
            nonlocal launches
            launches += 1
            first_started.set()
            await asyncio.sleep(0.01)
            return ProcessResult(
                config,
                None,
                None,
                "fake",
                1 if launches == 1 else 0,
                "stalled" if launches == 1 else None,
                0,
                None,
                None,
                None,
            )

        async def no_host_slot(_config: ProcessConfig) -> None:
            return None

        async def sibling() -> None:
            await first_started.wait()
            async with _leaf_slot(context):
                code_entered.set()
                await release_code.wait()

        monkeypatch.setattr(pool, "_build_status", bounded_status)
        monkeypatch.setattr(pool, "_launch_and_monitor", fake_launch)
        monkeypatch.setattr(pool, "_acquire_host_slot", no_host_slot)
        monkeypatch.setattr(run_parallel, "_POOL_FILL_POLL_INTERVAL_S", 0.01)
        monkeypatch.setattr(run_parallel, "compute_backoff", lambda *_args: 0.01)
        monkeypatch.setattr(run_parallel, "mark_failed_at", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(run_parallel, "_build_prepare_launch", lambda **_kwargs: None)
        monkeypatch.setattr(run_parallel, "compute_run_dir", lambda *_args: tmp_path)
        monkeypatch.setattr(
            run_parallel, "compute_task_state_dir", lambda *_args: tmp_path / "item"
        )
        monkeypatch.setattr(
            run_parallel, "_handle_success", lambda *args, **_kwargs: args[13].append((args[1], 0))
        )
        holder = asyncio.create_task(sibling())
        task = asyncio.create_task(
            run_parallel._run_agent_pool(
                spec=ProcessSpec(name="fixture"),
                step_def=ProcessStep(id="agent", mode="agent"),
                step="agent",
                each="ticker",
                variables={"RUN_ID": "test"},
                item_contexts=[{"ticker": "AFL"}],
                adapter_type="fixture",
                merged_config={},
                effective_outputs=None,
                effective_variant="",
                allowed_runtime=set(),
                retry_policy=RetryPolicy(max_retries=1),
                process_dir=tmp_path,
                refresh_token_fn=None,
                pool_config=pool._config,
                backend=None,
                out=MagicMock(),
                shared_pool=pool,
            )
        )
        try:
            await asyncio.wait_for(code_entered.wait(), timeout=2)

            async def retry_is_queued() -> None:
                while pool.snapshot.pending_retries == 0:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(retry_is_queued(), timeout=2)
            await asyncio.sleep(0.08)
            if task.done():
                await task
            assert launches == 1
            assert not holder.done()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not holder.done()
                assert not pool.shutting_down
            else:
                release_code.set()
                assert await asyncio.wait_for(task, timeout=2) == [("AFL", 0)]
                assert launches == 2
        finally:
            monkeypatch.setattr(pool, "_build_status", original_status)
            release_code.set()
            await asyncio.gather(holder, task, return_exceptions=True)
            await context.aclose()
        assert pool._semaphore.held == 0

    asyncio.run(check())
