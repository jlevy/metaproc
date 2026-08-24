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
    _execute_code_step,
    _execute_composite_step,
    _orchestrate,
)
from metaproc.errors import CLIError
from metaproc.models.authored import ProcessSpec, ProcessStep
from metaproc.models.plan import Plan, ResolvedStep


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


def test_command_steps_honor_executor_ceiling_above_asyncio_default(
    tmp_path: Path,
) -> None:
    worker_count = 33
    barrier = threading.Barrier(worker_count, timeout=5.0)

    def synchronized_command(
        *_args: object,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        barrier.wait()
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

    async def exercise() -> list[bool]:
        context = RunExecutionContext.create(max_concurrency=worker_count)
        try:
            steps = [
                ProcessStep(id=f"command-{index}", mode="code", command="true")
                for index in range(worker_count)
            ]
            with patch(
                "metaproc.commands.run_process.run_sampled_step_command",
                synchronized_command,
            ):
                return await asyncio.gather(
                    *(
                        _execute_code_step(
                            spec=ProcessSpec(name="test"),
                            step_def=step,
                            target=ResolvedStep(
                                step_id=step.id,
                                mode="code",
                                command="true",
                            ),
                            variables={},
                            process_dir=tmp_path,
                            run_dir=tmp_path / "run",
                            run_id="test/run-1",
                            execution_context=context,
                            out=_Out(),
                        )
                        for step in steps
                    )
                )
        finally:
            context.close()

    assert asyncio.run(exercise()) == [True] * worker_count


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
