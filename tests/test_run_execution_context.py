"""Run-wide execution context and synchronous-work concurrency tests."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
import pytest

from metaproc.commands.run_process import (
    RunExecutionContext,
    _execute_agent_step,
    _execute_code_step,
    _execute_composite_fan_out_step,
    _execute_composite_step,
    _leaf_slot,
    _orchestrate,
    _run_agent_subprocess,
    _run_sync,
)
from metaproc.errors import CLIError
from metaproc.io import read_yaml_file
from metaproc.io.state_io import read_attempt_history_at, read_status_at
from metaproc.models.authored import ForEach, ProcessSpec, ProcessStep, StepContext
from metaproc.models.plan import FanOut, Plan, ResolvedAdapter, ResolvedStep
from metaproc.models.runtime import AttemptDisposition
from metaproc.runpool.pool import ProcessConfig


class _Out:
    def progress(self, _message: str) -> None:
        pass


def test_composite_reuses_parent_execution_context(tmp_path: Path) -> None:
    child_spec_path = tmp_path / "child.process.md"
    child_spec_path.write_text(
        "---\nprocess:\n  name: child\n  steps: []\n---\n",
        encoding="utf-8",
    )
    step_def = ProcessStep(id="child", mode="composite", uses="deps.child")
    target = ResolvedStep(
        step_id="child",
        mode="composite",
        uses_path=str(child_spec_path),
    )
    captured: dict[str, object] = {}

    async def fake_orchestrate(**kwargs: object) -> None:
        captured["execution_context"] = kwargs["execution_context"]
        captured["scope_path"] = kwargs["scope_path"]
        captured["run_id"] = kwargs["run_id"]
        captured["run_dir"] = kwargs["run_dir"]
        captured["variables"] = kwargs["variables"]

    async def exercise() -> bool:
        context = RunExecutionContext.create(max_concurrency=2)
        try:
            with patch("metaproc.commands.run_process._orchestrate", fake_orchestrate):
                result = await _execute_composite_step(
                    step_def=step_def,
                    target=target,
                    variables={"RUN_ID": "run-1"},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run-1",
                    scope_path=(),
                    execution_context=context,
                    out=_Out(),
                )
            assert captured["execution_context"] is context
            assert captured["scope_path"] == ("child",)
            assert captured["run_id"] == "test/run-1/child"
            assert captured["run_dir"] == tmp_path / "run" / "child"
            child_variables = cast(dict[str, str], captured["variables"])
            assert child_variables["RUN_ID"] == "run-1/child"
            assert child_variables["run.dir"] == str((tmp_path / "run" / "child").resolve())
            return result
        finally:
            context.close()

    assert asyncio.run(exercise()) is True


def test_mapped_composite_scopes_share_run_context_and_leaf_ceiling(tmp_path: Path) -> None:
    roster_path = tmp_path / "roster.md"
    roster_path.write_text(
        "---\nprogress:\n  items:\n    - item: alfa\n    - item: brvo\n    - item: chrl\n---\n",
        encoding="utf-8",
    )
    child_spec_path = tmp_path / "child.process.md"
    child_spec_path.write_text(
        "---\nprocess:\n  name: child\n  steps: []\n---\n",
        encoding="utf-8",
    )
    step_def = ProcessStep(
        id="mapped-child",
        mode="composite",
        uses="deps.child",
        for_each=ForEach(
            over="deps.roster",
            bind="item",
            bind_fields=["item"],
            key="{{item}}",
        ),
    )
    target = ResolvedStep(
        step_id="mapped-child",
        mode="composite",
        uses_path=str(child_spec_path),
        fan_out=FanOut(
            over="deps.roster",
            bind="item",
            source=str(roster_path),
            bind_fields=["item"],
        ),
    )
    captured: list[RunExecutionContext] = []
    captured_keys: list[str] = []
    all_scopes_started = asyncio.Event()
    active_leaves = 0
    peak_leaves = 0
    events = MagicMock()

    async def fake_execute_composite_step(**kwargs: object) -> bool:
        nonlocal active_leaves, peak_leaves
        context = cast(RunExecutionContext, kwargs["execution_context"])
        variables = cast(dict[str, str], kwargs["variables"])
        captured.append(context)
        captured_keys.append(cast(str, kwargs["mapped_item_key"]))
        if len(captured) == 3:
            all_scopes_started.set()
        await asyncio.wait_for(all_scopes_started.wait(), timeout=0.5)
        if variables["item"] == "brvo":
            raise OSError("injected child-scope failure")
        async with _leaf_slot(context):
            active_leaves += 1
            peak_leaves = max(peak_leaves, active_leaves)
            await asyncio.sleep(0.01)
            active_leaves -= 1
        return True

    async def exercise() -> bool:
        context = RunExecutionContext.create(max_concurrency=1)
        try:
            with patch(
                "metaproc.commands.run_process._execute_composite_step",
                fake_execute_composite_step,
            ):
                result = await _execute_composite_fan_out_step(
                    step_def=step_def,
                    target=target,
                    variables={},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run-1",
                    scope_path=(),
                    execution_context=context,
                    events=events,
                    out=_Out(),
                )
            assert captured == [context, context, context]
            assert sorted(captured_keys) == ["alfa", "brvo", "chrl"]
            assert peak_leaves == 1
            state_root = tmp_path / "run" / ".state" / "tasks" / "mapped-child"
            statuses = {
                item: read_status_at(state_root / item) for item in ("alfa", "brvo", "chrl")
            }
            assert all(status is not None for status in statuses.values())
            assert statuses["alfa"] is not None and statuses["alfa"].state == "completed"
            assert statuses["brvo"] is not None and statuses["brvo"].state == "failed"
            assert statuses["chrl"] is not None and statuses["chrl"].state == "completed"
            return result
        finally:
            context.close()

    assert asyncio.run(exercise()) is False
    assert sorted(call.args[1] for call in events.item_start.call_args_list) == [
        "alfa",
        "brvo",
        "chrl",
    ]
    assert sorted(call.args[1] for call in events.item_complete.call_args_list) == [
        "alfa",
        "chrl",
    ]
    assert [call.args[1] for call in events.item_fail.call_args_list] == ["brvo"]


def test_mapped_composite_has_a_structural_scope_default(tmp_path: Path) -> None:
    roster_path = tmp_path / "roster.md"
    roster_path.write_text(
        "---\nprogress:\n  items:\n    - item: alfa\n---\n",
        encoding="utf-8",
    )
    child_spec_path = tmp_path / "child.process.md"
    child_spec_path.write_text(
        "---\nprocess:\n  name: child\n  steps: []\n---\n",
        encoding="utf-8",
    )
    step_def = ProcessStep(
        id="mapped-child",
        mode="composite",
        uses="deps.child",
        for_each=ForEach(
            over="deps.roster",
            bind="item",
            bind_fields=["item"],
            key="{{item}}",
        ),
    )
    target = ResolvedStep(
        step_id="mapped-child",
        mode="composite",
        uses_path=str(child_spec_path),
        fan_out=FanOut(
            over="deps.roster",
            bind="item",
            source=str(roster_path),
            bind_fields=["item"],
        ),
    )
    run_fan_out = AsyncMock(return_value=(1, 1))

    async def exercise() -> bool:
        context = RunExecutionContext.create(max_concurrency=None)
        try:
            with patch("metaproc.commands.run_process.run_fan_out", run_fan_out):
                return await _execute_composite_fan_out_step(
                    step_def=step_def,
                    target=target,
                    variables={},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run-1",
                    scope_path=(),
                    execution_context=context,
                    out=_Out(),
                )
        finally:
            context.close()

    assert asyncio.run(exercise()) is True
    assert run_fan_out.await_args is not None
    scope_ceiling = run_fan_out.await_args.kwargs["max_concurrency"]
    assert isinstance(scope_ceiling, int) and scope_ceiling > 0


def test_mapped_composite_cancellation_ends_parent_attempt(tmp_path: Path) -> None:
    roster_path = tmp_path / "roster.md"
    roster_path.write_text(
        "---\nprogress:\n  items:\n    - item: alfa\n---\n",
        encoding="utf-8",
    )
    child_spec_path = tmp_path / "child.process.md"
    child_spec_path.write_text(
        "---\nprocess:\n  name: child\n  steps: []\n---\n",
        encoding="utf-8",
    )
    step_def = ProcessStep(
        id="mapped-child",
        mode="composite",
        uses="deps.child",
        for_each=ForEach(
            over="deps.roster",
            bind="item",
            bind_fields=["item"],
            key="{{item}}",
        ),
    )
    target = ResolvedStep(
        step_id="mapped-child",
        mode="composite",
        uses_path=str(child_spec_path),
        fan_out=FanOut(
            over="deps.roster",
            bind="item",
            source=str(roster_path),
            bind_fields=["item"],
        ),
    )
    child_started = asyncio.Event()

    async def wait_forever(**_kwargs: object) -> bool:
        child_started.set()
        await asyncio.Event().wait()
        return True

    async def exercise() -> None:
        context = RunExecutionContext.create(max_concurrency=1)
        try:
            with patch(
                "metaproc.commands.run_process._execute_composite_step",
                wait_forever,
            ):
                task = asyncio.create_task(
                    _execute_composite_fan_out_step(
                        step_def=step_def,
                        target=target,
                        variables={},
                        process_dir=tmp_path,
                        run_dir=tmp_path / "run",
                        run_id="test/run-1",
                        scope_path=(),
                        execution_context=context,
                        out=_Out(),
                    )
                )
                await asyncio.wait_for(child_started.wait(), timeout=1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            context.close()

    asyncio.run(exercise())
    state_dir = tmp_path / "run" / ".state" / "tasks" / "mapped-child" / "alfa"
    status = read_status_at(state_dir)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "mapped child process cancelled"
    history = read_attempt_history_at(state_dir)
    assert [attempt.disposition for attempt in history] == [AttemptDisposition.cancelled]


def test_mapped_composite_rejects_gcp_worker_partitioning(tmp_path: Path) -> None:
    step_def = ProcessStep(
        id="mapped-child",
        mode="composite",
        uses="deps.child",
        for_each=ForEach(
            over="deps.roster",
            bind="item",
            bind_fields=["item"],
            key="{{item}}",
        ),
    )
    target = ResolvedStep(
        step_id="mapped-child",
        mode="composite",
        fan_out=FanOut(
            over="deps.roster",
            bind="item",
            source=str(tmp_path / "roster.md"),
            bind_fields=["item"],
        ),
    )

    async def exercise() -> None:
        context = RunExecutionContext.create(backend_name="gcp-worker", max_concurrency=None)
        try:
            with pytest.raises(CLIError, match="does not yet support gcp-worker"):
                await _execute_composite_fan_out_step(
                    step_def=step_def,
                    target=target,
                    variables={},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run-1",
                    scope_path=(),
                    execution_context=context,
                    out=_Out(),
                )
        finally:
            context.close()

    asyncio.run(exercise())


def test_recursive_evaluator_accepts_only_scope_local_arguments() -> None:
    assert set(inspect.signature(_orchestrate).parameters) == {
        "spec",
        "plan",
        "variables",
        "process_path",
        "process_dir",
        "run_dir",
        "run_id",
        "scope_path",
        "execution_context",
        "out",
        "events",
    }


def _one_step_process() -> tuple[ProcessSpec, Plan]:
    return (
        ProcessSpec(
            name="child",
            steps=[ProcessStep(id="leaf", mode="code", command="true")],
        ),
        Plan(
            generated_at="2026-08-24T00:00:00Z",
            process="child",
            params={},
            steps=[ResolvedStep(step_id="leaf", mode="code", command="true")],
        ),
    )


def test_nested_scope_uses_global_force_without_reusing_root_skip(
    tmp_path: Path,
) -> None:
    spec, plan = _one_step_process()
    execute = AsyncMock(return_value=True)
    invalidate = MagicMock(return_value=[])

    async def exercise() -> None:
        context = RunExecutionContext.create(
            max_concurrency=1,
            skip_steps={"leaf"},
            force=True,
        )
        try:
            with (
                patch("metaproc.commands.run_process._execute_step", execute),
                patch("metaproc.commands.run_process._invalidate_downstream", invalidate),
            ):
                await _orchestrate(
                    spec=spec,
                    plan=plan,
                    variables={},
                    process_path=tmp_path / "child.process.md",
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run-1/child",
                    scope_path=("child",),
                    execution_context=context,
                    out=_Out(),
                    events=MagicMock(),
                )
        finally:
            context.close()

    asyncio.run(exercise())
    execute.assert_awaited_once()
    invalidate.assert_called_once()


def test_nested_scope_uses_continue_on_step_failure_policy(tmp_path: Path) -> None:
    spec, plan = _one_step_process()
    execute = AsyncMock(return_value=False)

    async def exercise() -> None:
        context = RunExecutionContext.create(
            max_concurrency=1,
            continue_on_error=False,
            continue_on_step_failure=True,
        )
        try:
            with (
                patch("metaproc.commands.run_process._execute_step", execute),
                pytest.raises(CLIError, match="Process completed with failures: leaf"),
            ):
                await _orchestrate(
                    spec=spec,
                    plan=plan,
                    variables={},
                    process_path=tmp_path / "child.process.md",
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run-1/child",
                    scope_path=("child",),
                    execution_context=context,
                    out=_Out(),
                    events=MagicMock(),
                )
        finally:
            context.close()

    asyncio.run(exercise())
    execute.assert_awaited_once()


def test_sync_executor_is_independent_of_leaf_ceiling() -> None:
    first_started = threading.Event()
    second_started = threading.Event()

    def first() -> str:
        first_started.set()
        if not second_started.wait(timeout=2.0):
            raise AssertionError("second executor worker did not start")
        return "first"

    def second() -> str:
        if not first_started.wait(timeout=2.0):
            raise AssertionError("first executor worker did not start")
        second_started.set()
        return "second"

    async def exercise() -> list[str]:
        context = RunExecutionContext.create(max_concurrency=1)
        try:
            return list(
                await asyncio.gather(
                    _run_sync(context, first),
                    _run_sync(context, second),
                )
            )
        finally:
            context.close()

    assert asyncio.run(exercise()) == ["first", "second"]


@pytest.mark.parametrize(
    ("max_concurrency", "expected_workers"),
    [
        (None, 32),
        (1, 32),
        (32, 32),
        (40, 40),
    ],
)
def test_sync_executor_never_floors_explicit_leaf_ceiling(
    max_concurrency: int | None,
    expected_workers: int,
) -> None:
    context = RunExecutionContext.create(max_concurrency=max_concurrency)
    try:
        assert context.sync_executor._max_workers == expected_workers  # noqa: SLF001
    finally:
        context.close()


def test_leaf_slot_does_not_synthesize_cancellation() -> None:
    async def exercise() -> bool:
        context = RunExecutionContext.create(max_concurrency=1)
        context.cancellation_event.set()
        try:
            async with _leaf_slot(context):
                return True
        finally:
            context.close()

    assert asyncio.run(exercise()) is True


def test_close_waits_for_started_sync_work() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def work() -> None:
        started.set()
        release.wait(timeout=2.0)
        finished.set()

    context = RunExecutionContext.create(max_concurrency=1)
    context.sync_executor.submit(work)
    assert started.wait(timeout=2.0)
    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        context.close()
    finally:
        timer.join(timeout=2.0)

    assert finished.is_set()


def test_invalid_leaf_ceiling_is_a_cli_error() -> None:
    with pytest.raises(CLIError, match="max_concurrency must be at least 1"):
        RunExecutionContext.create(max_concurrency=0)


def test_recursive_siblings_share_one_executable_leaf_ceiling(tmp_path: Path) -> None:
    child_spec_path = tmp_path / "child.process.md"
    child_spec_path.write_text(
        "---\nprocess:\n  name: child\n  steps:\n"
        "    - id: leaf\n      mode: code\n      command: 'true'\n---\n",
        encoding="utf-8",
    )
    lock = threading.Lock()
    active = 0
    peak = 0

    def measured_command(
        *_args: object,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    async def exercise() -> list[bool]:
        context = RunExecutionContext.create(max_concurrency=1)
        try:
            with patch(
                "metaproc.commands.run_process.run_sampled_step_command",
                measured_command,
            ):
                return await asyncio.gather(
                    *(
                        _execute_composite_step(
                            step_def=ProcessStep(
                                id=step_id,
                                mode="composite",
                                uses="deps.child",
                            ),
                            target=ResolvedStep(
                                step_id=step_id,
                                mode="composite",
                                uses_path=str(child_spec_path),
                            ),
                            variables={},
                            process_dir=tmp_path,
                            run_dir=tmp_path / "run",
                            run_id="test/run-1",
                            scope_path=(),
                            execution_context=context,
                            out=_Out(),
                        )
                        for step_id in ("left", "right")
                    )
                )
        finally:
            context.close()

    assert asyncio.run(exercise()) == [True, True]
    assert peak == 1


def test_scalar_agent_steps_share_one_executable_leaf_ceiling(tmp_path: Path) -> None:
    active = 0
    peak = 0
    adapter = MagicMock()
    adapter.build_command.return_value = ["fake-adapter"]
    adapter.prepare_env.side_effect = lambda env, _config: env
    adapter.working_directory.return_value = None

    async def measured_agent(*_args: object, **kwargs: object) -> int:
        nonlocal active, peak
        log_path = kwargs["log_path"]
        assert isinstance(log_path, Path)
        log_path.write_text("")
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return 0

    async def exercise() -> list[bool]:
        context = RunExecutionContext.create(max_concurrency=1)
        try:
            with (
                patch("metaproc.commands.run_process.get_adapter", return_value=adapter),
                patch(
                    "metaproc.commands.run_process._run_agent_subprocess",
                    side_effect=measured_agent,
                ),
                patch(
                    "metaproc.commands.run_process.capture_repo_snapshot",
                    return_value=None,
                ),
            ):
                return await asyncio.gather(
                    *(
                        _execute_agent_step(
                            spec=ProcessSpec(name="test"),
                            step_def=ProcessStep(
                                id=step_id,
                                mode="agent",
                                prompt_prefix="test",
                            ),
                            target=ResolvedStep(
                                step_id=step_id,
                                mode="agent",
                                adapter=ResolvedAdapter(type="test", config={}),
                            ),
                            variables={},
                            process_dir=tmp_path,
                            run_dir=tmp_path / "run",
                            run_id="test/run-1",
                            backend_name="gcp-worker",
                            execution_context=context,
                            out=_Out(),
                        )
                        for step_id in ("left", "right")
                    )
                )
        finally:
            context.close()

    assert asyncio.run(exercise()) == [True, True]
    assert peak == 1


def test_scalar_agent_steps_use_one_run_owned_pool(tmp_path: Path) -> None:
    adapter = MagicMock()
    adapter.build_command.return_value = ["fake-adapter"]
    adapter.prepare_env.side_effect = lambda env, _config: env
    adapter.working_directory.return_value = None
    submitted: list[ProcessConfig] = []
    pool = MagicMock()
    pool.shutdown = AsyncMock()

    def submit(config: ProcessConfig) -> asyncio.Future[Any]:
        submitted.append(config)
        launch = config.launch
        assert launch is not None and launch.log_path is not None
        launch.log_path.write_text("", encoding="utf-8")
        future = asyncio.get_running_loop().create_future()
        future.set_result(SimpleNamespace(exit_code=0, kill_reason=None))
        return future

    pool.submit.side_effect = submit

    @asynccontextmanager
    async def no_host_admission(**_kwargs: object) -> AsyncGenerator[None]:
        yield None

    async def exercise() -> list[bool]:
        context = RunExecutionContext.create(
            max_concurrency=2,
            run_dir=tmp_path / "run",
            enable_run_pool=True,
        )
        direct_launch = AsyncMock(side_effect=AssertionError("direct launch used"))
        pool_factory = MagicMock(return_value=pool)
        try:
            with (
                patch("metaproc.commands.run_process.RunPool", pool_factory),
                patch("metaproc.commands.run_process.get_adapter", return_value=adapter),
                patch(
                    "metaproc.commands.run_process._run_agent_subprocess",
                    direct_launch,
                ),
                patch(
                    "metaproc.commands.run_process.admitted_launch",
                    no_host_admission,
                ),
                patch(
                    "metaproc.commands.run_process.capture_repo_snapshot",
                    return_value=None,
                ),
            ):
                results = await asyncio.gather(
                    *(
                        _execute_agent_step(
                            spec=ProcessSpec(name="test"),
                            step_def=ProcessStep(
                                id=step_id,
                                mode="agent",
                                prompt_prefix="test",
                            ),
                            target=ResolvedStep(
                                step_id=step_id,
                                mode="agent",
                                adapter=ResolvedAdapter(type="test", config={}),
                            ),
                            variables={},
                            process_dir=tmp_path,
                            run_dir=tmp_path / "run",
                            run_id="test/run-1",
                            execution_context=context,
                            out=_Out(),
                        )
                        for step_id in ("left", "right")
                    )
                )
            assert direct_launch.await_count == 0
            assert pool_factory.call_count == 1
            return results
        finally:
            await context.aclose()

    assert asyncio.run(exercise()) == [True, True]
    assert len(submitted) == 2
    assert {config.label for config in submitted} == {
        "test/run-1/left",
        "test/run-1/right",
    }
    assert all(config.execution_profile == "test" for config in submitted)
    pool.shutdown.assert_awaited_once()


def test_run_context_closes_executor_when_pool_shutdown_fails(tmp_path: Path) -> None:
    context = RunExecutionContext.create(
        max_concurrency=None,
        run_dir=tmp_path / "run",
        enable_run_pool=True,
    )
    assert context.run_pool_owner is not None
    pool = MagicMock()
    pool.shutdown = AsyncMock(side_effect=OSError("injected pool shutdown failure"))
    cast(Any, context.run_pool_owner).pool = pool

    with pytest.raises(OSError, match="injected pool shutdown failure"):
        asyncio.run(context.aclose())

    with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
        context.sync_executor.submit(lambda: None)


def _write_blocking_process_tree_script(
    tmp_path: Path,
    *,
    child_ignores_sigterm: bool = False,
    leader_exits: bool = False,
) -> Path:
    script = tmp_path / "blocking-process-tree.py"
    child_program = "import time; print('ready', flush=True); time.sleep(60)"
    if child_ignores_sigterm:
        child_program = (
            "import signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); time.sleep(60)"
        )
    script.write_text(
        inspect.cleandoc(
            f"""
            import json
            import os
            import subprocess
            import sys
            import time
            from pathlib import Path

            child = subprocess.Popen(
                [sys.executable, "-c", {child_program!r}],
                stdout=subprocess.PIPE,
                text=True,
            )
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "ready"
            Path(sys.argv[1]).write_text(
                json.dumps({{"parent": os.getpid(), "child": child.pid}})
            )
            if {leader_exits!r}:
                time.sleep(0.2)
                raise SystemExit(0)
            time.sleep(60)
            """
        )
        + "\n",
        encoding="utf-8",
    )
    return script


async def _wait_for_process_tree(path: Path) -> dict[str, int]:
    for _ in range(200):
        try:
            return {key: int(value) for key, value in json.loads(path.read_text()).items()}
        except (FileNotFoundError, json.JSONDecodeError):
            await asyncio.sleep(0.01)
    raise AssertionError(f"process tree did not publish its pids at {path}")


def _live_processes(pids: dict[str, int]) -> set[int]:
    live: set[int] = set()
    for pid in pids.values():
        try:
            process = psutil.Process(pid)
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                live.add(pid)
        except psutil.NoSuchProcess:
            continue
    return live


def _kill_processes(pids: dict[str, int]) -> None:
    processes: list[psutil.Process] = []
    for pid in pids.values():
        try:
            process = psutil.Process(pid)
            processes.extend(process.children(recursive=True))
            processes.append(process)
        except psutil.NoSuchProcess:
            continue
    for process in reversed(processes):
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            process.kill()
    if processes:
        psutil.wait_procs(processes, timeout=2)


def test_cancelling_agent_subprocess_kills_tree_before_releasing_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _write_blocking_process_tree_script(tmp_path, child_ignores_sigterm=True)
    monkeypatch.setattr("metaproc.runpool.backend._PROCESS_TERMINATION_GRACE_S", 0.05)
    pids_path = tmp_path / "agent-pids.json"
    context = RunExecutionContext.create(max_concurrency=1)
    pids: dict[str, int] = {}
    release_observations: list[set[int]] = []

    @asynccontextmanager
    async def tracked_host_admission():
        try:
            yield
        finally:
            release_observations.append(_live_processes(pids))

    async def exercise() -> None:
        nonlocal pids

        async def run_leaf() -> int:
            async with _leaf_slot(context):
                async with tracked_host_admission():
                    return await _run_agent_subprocess(
                        [sys.executable, str(script), str(pids_path)],
                        env={},
                        cwd=tmp_path,
                        log_path=tmp_path / "agent.log",
                        timeout_s=None,
                        use_filter=False,
                        execution_context=context,
                    )

        task = asyncio.create_task(run_leaf())
        pids = await _wait_for_process_tree(pids_path)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    try:
        asyncio.run(exercise())
        assert release_observations == [set()]
        assert _live_processes(pids) == set()
        assert context.cancellation_event.is_set()
        assert context.leaf_semaphore is not None
        assert context.leaf_semaphore._value == 1  # noqa: SLF001 - ownership invariant
    finally:
        _kill_processes(pids)
        context.close()


def test_agent_subprocess_timeout_kills_its_process_tree(tmp_path: Path) -> None:
    script = _write_blocking_process_tree_script(tmp_path)
    pids_path = tmp_path / "timeout-pids.json"
    pids: dict[str, int] = {}

    async def exercise() -> None:
        nonlocal pids
        task = asyncio.create_task(
            _run_agent_subprocess(
                [sys.executable, str(script), str(pids_path)],
                env={},
                cwd=tmp_path,
                log_path=tmp_path / "timeout.log",
                timeout_s=0.2,
                use_filter=False,
            )
        )
        pids = await _wait_for_process_tree(pids_path)
        with pytest.raises(subprocess.TimeoutExpired):
            await task

    try:
        asyncio.run(exercise())
        assert _live_processes(pids) == set()
    finally:
        _kill_processes(pids)


def test_agent_subprocess_cleans_tree_after_leader_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _write_blocking_process_tree_script(
        tmp_path,
        child_ignores_sigterm=True,
        leader_exits=True,
    )
    monkeypatch.setattr("metaproc.runpool.backend._PROCESS_TERMINATION_GRACE_S", 0.05)
    pids_path = tmp_path / "exited-agent-pids.json"
    pids: dict[str, int] = {}

    async def exercise() -> int:
        nonlocal pids
        task = asyncio.create_task(
            _run_agent_subprocess(
                [sys.executable, str(script), str(pids_path)],
                env={},
                cwd=tmp_path,
                log_path=tmp_path / "exited-agent.log",
                timeout_s=2,
                use_filter=False,
            )
        )
        pids = await _wait_for_process_tree(pids_path)
        return await task

    try:
        assert asyncio.run(exercise()) == 0
        assert _live_processes(pids) == set()
    finally:
        _kill_processes(pids)


def test_agent_subprocess_filter_flushes_before_return(tmp_path: Path) -> None:
    log_path = tmp_path / "filtered.log"

    async def exercise() -> int:
        return await _run_agent_subprocess(
            [sys.executable, "-c", "print('kept output')"],
            env={},
            cwd=tmp_path,
            log_path=log_path,
            timeout_s=2,
            use_filter=True,
        )

    assert asyncio.run(exercise()) == 0
    assert log_path.read_text() == "kept output\n"


def test_cancelling_code_command_kills_tree_before_releasing_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _write_blocking_process_tree_script(tmp_path, child_ignores_sigterm=True)
    monkeypatch.setattr(
        "metaproc.engine.resource_sampling._PROCESS_TERMINATION_GRACE_S",
        0.05,
    )
    pids_path = tmp_path / "command-pids.json"
    context = RunExecutionContext.create(max_concurrency=1)
    pids: dict[str, int] = {}
    release_observations: list[set[int]] = []
    step = ProcessStep(
        id="blocking-command",
        mode="code",
        command=shlex.join([sys.executable, str(script), str(pids_path)]),
    )
    target = ResolvedStep(
        step_id=step.id,
        mode="code",
        command=step.command,
    )

    async def exercise() -> None:
        nonlocal pids

        async def run_leaf() -> bool:
            async with _leaf_slot(context):
                try:
                    return await _execute_code_step(
                        spec=ProcessSpec(name="test"),
                        step_def=step,
                        target=target,
                        variables={},
                        process_dir=tmp_path,
                        run_dir=tmp_path / "run",
                        run_id="test/run-1",
                        execution_context=context,
                        out=_Out(),
                    )
                finally:
                    release_observations.append(_live_processes(pids))

        task = asyncio.create_task(run_leaf())
        pids = await _wait_for_process_tree(pids_path)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    try:
        asyncio.run(exercise())
        assert release_observations == [set()]
        assert _live_processes(pids) == set()
        assert context.cancellation_event.is_set()
        assert context.leaf_semaphore is not None
        assert context.leaf_semaphore._value == 1  # noqa: SLF001 - ownership invariant
    finally:
        _kill_processes(pids)
        context.close()


def test_code_handler_can_observe_cooperative_cancellation(tmp_path: Path) -> None:
    context = RunExecutionContext.create(max_concurrency=1)
    handler_started = threading.Event()
    handler_observed_cancel = threading.Event()
    step = ProcessStep(id="blocking-handler", mode="code", handler="unused:handler")
    target = ResolvedStep(
        step_id=step.id,
        mode="code",
        handler=step.handler,
    )

    def handler(handler_context: StepContext, _step: ProcessStep) -> None:
        handler_started.set()
        while not handler_context.cancel_requested():
            time.sleep(0.01)
        handler_observed_cancel.set()

    async def exercise() -> None:
        with patch("metaproc.commands.run_process.resolve_code_handler", return_value=handler):
            task = asyncio.create_task(
                _execute_code_step(
                    spec=ProcessSpec(name="test"),
                    step_def=step,
                    target=target,
                    variables={},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run-1",
                    execution_context=context,
                    out=_Out(),
                )
            )
            assert await asyncio.to_thread(handler_started.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)

    try:
        asyncio.run(exercise())
        assert handler_observed_cancel.is_set()
        assert context.cancellation_event.is_set()
    finally:
        context.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal contract")
def test_real_sigint_cancels_run_and_cleans_command_tree(tmp_path: Path) -> None:
    script = _write_blocking_process_tree_script(tmp_path, child_ignores_sigterm=True)
    pids_path = tmp_path / "sigint-pids.json"
    runs_dir = tmp_path / "runs"
    run_id = "sigint-run"
    command = shlex.join([sys.executable, str(script), str(pids_path)])
    process_path = tmp_path / "sigint.process.md"
    process_path.write_text(
        "---\n"
        "process:\n"
        "  name: sigint-smoke\n"
        "  steps:\n"
        "    - id: hold\n"
        "      mode: code\n"
        f"      command: {json.dumps(command)}\n"
        "---\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "metaproc",
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            f"RUN_ID={run_id}",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    pids: dict[str, int] = {}
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                pids = {key: int(value) for key, value in json.loads(pids_path.read_text()).items()}
                break
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.02)
        assert pids, "run-process did not launch its command tree"

        os.kill(process.pid, signal.SIGINT)
        output, _ = process.communicate(timeout=10)

        assert process.returncode != 0, output
        assert _live_processes(pids) == set()
        run_dir = runs_dir / run_id
        status = read_yaml_file(run_dir / ".state" / "process-status.yaml")
        assert status["state"] == "cancelled"
        assert status["steps"]["hold"]["state"] == "cancelled"
        assert not (run_dir / ".state" / "orchestrator-lease.yaml").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        _kill_processes(pids)


def test_code_command_cleans_tree_after_leader_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _write_blocking_process_tree_script(
        tmp_path,
        child_ignores_sigterm=True,
        leader_exits=True,
    )
    monkeypatch.setattr(
        "metaproc.engine.resource_sampling._PROCESS_TERMINATION_GRACE_S",
        0.05,
    )
    pids_path = tmp_path / "exited-command-pids.json"
    pids: dict[str, int] = {}
    step = ProcessStep(
        id="exited-command",
        mode="code",
        command=shlex.join([sys.executable, str(script), str(pids_path)]),
    )

    async def exercise() -> bool:
        nonlocal pids
        task = asyncio.create_task(
            _execute_code_step(
                spec=ProcessSpec(name="test"),
                step_def=step,
                target=ResolvedStep(
                    step_id=step.id,
                    mode="code",
                    command=step.command,
                ),
                variables={},
                process_dir=tmp_path,
                run_dir=tmp_path / "run",
                run_id="test/run-1",
                out=_Out(),
            )
        )
        pids = await _wait_for_process_tree(pids_path)
        return await task

    try:
        assert asyncio.run(exercise()) is True
        assert _live_processes(pids) == set()
    finally:
        _kill_processes(pids)
