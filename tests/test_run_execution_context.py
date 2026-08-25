"""Run-wide execution context and synchronous-work concurrency tests."""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from metaproc.commands.run_process import (
    RunExecutionContext,
    _execute_agent_step,
    _execute_composite_step,
    _leaf_slot,
    _orchestrate,
    _run_sync,
)
from metaproc.errors import CLIError
from metaproc.models.authored import ProcessSpec, ProcessStep
from metaproc.models.plan import Plan, ResolvedAdapter, ResolvedStep


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
            return result
        finally:
            context.close()

    assert asyncio.run(exercise()) is True


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
