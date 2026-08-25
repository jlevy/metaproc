"""Tests for metaproc run-process — DAG orchestration command.

Tests cover:
- Dry-run output (topological levels, step modes)
- Step dispatcher routing (code, agent, fan-out, manual, composite)
- Completion detection + skip of completed steps
- Downstream invalidation on --force
- Ancestor verification for --from/--only
- Process-status.yaml generation
- Error handling and --continue-on-error
"""

from __future__ import annotations

import asyncio
import inspect
import json
import shlex
import subprocess
import sys
import textwrap
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.cli import app
from metaproc.commands.run_process import run_process_command
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags
from metaproc.dispatch.pool_dispatch import PoolDispatchConfig
from metaproc.engine.dep_state import fingerprint_step
from metaproc.errors import CLIError
from metaproc.io import read_yaml_file
from metaproc.io.state_io import write_result_at
from metaproc.logutil.resource_events import read_events
from metaproc.models.resource_budget import FinalizationState
from metaproc.models.resources import ResourcesDocument, SampleEvent, read_resources_document
from metaproc.models.runtime import ResultRecord
from metaproc.runpool.backend import LaunchBackend
from metaproc.runpool.pool import RunPool, RunPoolConfig

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SYNTHETIC_PROCESS = str(
    _REPO_ROOT / "tests" / "fixtures" / "fingerprint_smoke" / "fingerprint-smoke.process.md"
)

from metaproc.commands.helpers import validate_gcp_worker_topology
from metaproc.commands.run_process import (
    RunExecutionContext,
    _execute_code_step,
    _execute_composite_step,
    _execute_fan_out_step,
    _execute_gcp_worker_dispatch,
    _execute_manual_step,
    _finish_deferred_fan_out_attempts,
    _invalidate_downstream,
    _is_step_completed,
    _maybe_cascade_for_fingerprint,
    _orchestrate,
    _preflight_plan_adapters,
    _read_recorded_step_hash,
    _read_step_status,
    _run_agent_subprocess,
    _verify_ancestors,
    _write_process_status,
)
from metaproc.engine.discovery import FanOutDiscovery
from metaproc.engine.graph import topo_sort
from metaproc.engine.pathing import compute_task_state_dir
from metaproc.engine.write_boundary import RepoSnapshot, WriteTarget
from metaproc.io.state_io import (
    mark_completed_at,
    mark_running_at,
    read_attempt_history_at,
    read_manual_ack_at,
    read_status_at,
    write_manual_ack_at,
    write_status_at,
)
from metaproc.models.authored import ForEach, IOSpec, ProcessSpec, ProcessStep
from metaproc.models.plan import FanOut, Plan, ResolvedAdapter, ResolvedStep
from metaproc.models.runtime import (
    AttemptDisposition,
    ManualAckRecord,
    StatusRecord,
)
from metaproc.paths import LOGS_DIR, ORCHESTRATOR_LEASE_FILE, STATE_DIR, STATUS_FILE
from metaproc.paths import STATE_DIR as _STATE_DIR
from metaproc.paths import TASKS_SUBDIR as _TASKS_SUBDIR
from metaproc.runpool.process_events import ProcessEventLogger


class FakeOut:
    """Minimal stand-in for the ``out`` progress-reporting object."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.warnings: list[str] = []

    def progress(self, msg: str) -> None:
        self.messages.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)


def test_adapter_preflight_skips_active_steps_without_agent_leaves() -> None:
    adapter = MagicMock()
    adapter.preflight.return_value = "unexpected drift warning"
    plan = Plan(
        process="adapterless",
        steps=[
            ResolvedStep(step_id="prepare", mode="code"),
            ResolvedStep(step_id="scope", mode="composite"),
        ],
    )

    with patch("metaproc.commands.run_process.get_adapter", return_value=adapter):
        messages = _preflight_plan_adapters(
            plan,
            active_step_ids={"prepare", "scope"},
        )

    assert messages == []
    adapter.preflight.assert_not_called()


def test_adapter_preflight_checks_each_active_agent_adapter_once() -> None:
    adapter = MagicMock()
    adapter.preflight.return_value = "agent drift warning"
    plan = Plan(
        process="agents",
        steps=[
            ResolvedStep(step_id="first", mode="agent"),
            ResolvedStep(step_id="second", mode="agent"),
            ResolvedStep(step_id="inactive", mode="agent"),
        ],
    )

    with patch("metaproc.commands.run_process.get_adapter", return_value=adapter):
        messages = _preflight_plan_adapters(
            plan,
            active_step_ids={"first", "second"},
        )

    assert messages == ["agent drift warning"]
    adapter.preflight.assert_called_once_with()


@contextmanager
def _test_execution_context(
    *,
    max_concurrency: int | None = None,
    variant_override: str | None = None,
    profile_files: Sequence[Path] = (),
    pool_dispatch_template: PoolDispatchConfig | None = None,
) -> Generator[RunExecutionContext, None, None]:
    context = RunExecutionContext.create(
        max_concurrency=max_concurrency,
        variant_override=variant_override,
        profile_files=profile_files,
        pool_dispatch_template=pool_dispatch_template,
    )
    try:
        yield context
    finally:
        context.close()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("deadline"), FinalizationState.TIMED_OUT),
        (RuntimeError("boom"), FinalizationState.FAILED),
    ],
)
def test_run_process_passes_causal_failure_to_resource_finalizer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected: FinalizationState,
) -> None:
    process_path = tmp_path / "causal.process.md"
    process_path.write_text(
        "---\nprocess:\n  name: causal\n  steps:\n    - id: noop\n"
        "      mode: code\n      command: 'true'\n---\n"
    )

    async def fail_orchestration(**_kwargs: object) -> None:
        raise error

    monkeypatch.setattr("metaproc.commands.run_process._orchestrate", fail_orchestration)
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=run-1",
        ],
    )

    assert result.exit_code != 0
    document = read_resources_document(tmp_path / "runs" / "run-1" / "resources.json")
    assert isinstance(document, ResourcesDocument)
    assert document.finalization is not None
    assert document.finalization.state is expected
    assert document.finalization.terminal_error_type == type(error).__name__


def test_run_process_preserves_original_failure_and_releases_lease_when_finalizer_interrupts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_path = tmp_path / "causal.process.md"
    process_path.write_text(
        "---\nprocess:\n  name: causal\n  steps:\n    - id: noop\n"
        "      mode: code\n      command: 'true'\n---\n"
    )

    original_error = RuntimeError("orchestration failed")

    async def fail_orchestration(**_kwargs: object) -> None:
        raise original_error

    def interrupt_finalizer(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    released: list[Path] = []
    monkeypatch.setattr("metaproc.commands.run_process._orchestrate", fail_orchestration)
    monkeypatch.setattr(
        "metaproc.commands.run_process.finalize_run_resources",
        interrupt_finalizer,
    )
    monkeypatch.setattr(
        "metaproc.commands.run_process.release_lease",
        lambda run_dir: released.append(run_dir),
    )

    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=run-1",
        ],
    )

    assert result.exception is original_error
    assert released == [tmp_path / "runs" / "run-1"]


def test_run_process_preserves_original_failure_when_pool_shutdown_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_path = tmp_path / "causal.process.md"
    process_path.write_text(
        "---\nprocess:\n  name: causal\n  steps:\n    - id: noop\n"
        "      mode: code\n      command: 'true'\n---\n"
    )
    original_error = RuntimeError("orchestration failed")

    async def fail_orchestration(**_kwargs: object) -> None:
        raise original_error

    context = RunExecutionContext.create(
        max_concurrency=None,
        run_dir=tmp_path / "runs" / "run-1",
        enable_run_pool=True,
    )
    assert context.run_pool_owner is not None
    pool = MagicMock()
    pool.shutdown = AsyncMock(side_effect=OSError("pool shutdown failed"))
    context.run_pool_owner.pool = pool

    monkeypatch.setattr("metaproc.commands.run_process._orchestrate", fail_orchestration)
    monkeypatch.setattr(
        "metaproc.commands.run_process.RunExecutionContext.create",
        lambda **_kwargs: context,
    )

    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=run-1",
        ],
    )

    assert result.exception is original_error
    assert getattr(original_error, "__notes__", []) == [
        "run-owned execution cleanup also failed: OSError('pool shutdown failed')"
    ]
    pool.shutdown.assert_awaited_once()


def test_agent_subprocesses_do_not_block_the_dag_event_loop(tmp_path: Path) -> None:
    """Independent non-fan-out agent steps can occupy the same DAG level."""
    barrier_script = tmp_path / "barrier.py"
    barrier_script.write_text(
        textwrap.dedent(
            """
            import sys
            import time
            from pathlib import Path

            mine = Path(sys.argv[1])
            other = Path(sys.argv[2])
            mine.write_text("ready")
            deadline = time.monotonic() + 1
            while not other.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            raise SystemExit(0 if other.exists() else 1)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    one_ready = tmp_path / "one.ready"
    two_ready = tmp_path / "two.ready"

    async def run_both() -> tuple[int, int]:
        return await asyncio.gather(
            _run_agent_subprocess(
                [sys.executable, str(barrier_script), str(one_ready), str(two_ready)],
                env={},
                cwd=tmp_path,
                log_path=tmp_path / "one.log",
                timeout_s=2,
                use_filter=False,
            ),
            _run_agent_subprocess(
                [sys.executable, str(barrier_script), str(two_ready), str(one_ready)],
                env={},
                cwd=tmp_path,
                log_path=tmp_path / "two.log",
                timeout_s=2,
                use_filter=False,
            ),
        )

    assert asyncio.run(run_both()) == [0, 0]


# ── Helpers ───────────────────────────────────────────────────────


def _step(
    id: str,
    needs: list[str] | None = None,
    mode: Literal["code", "agent", "composite", "manual"] = "code",
) -> ResolvedStep:
    """Minimal ResolvedStep for orchestration tests."""
    return ResolvedStep(
        step_id=id,
        mode=mode,
        adapter=ResolvedAdapter(type="test", config={}),
        needs=needs or [],
    )


def _make_plan(*steps: ResolvedStep) -> Plan:
    return Plan(
        generated_at="2026-04-09T00:00:00",
        process="test",
        params={},
        steps=list(steps),
    )


def _make_spec(*step_ids: str) -> ProcessSpec:
    """Minimal ProcessSpec with agent steps for the given IDs."""
    return ProcessSpec(
        name="test",
        steps=[ProcessStep(id=sid, mode="agent") for sid in step_ids],
    )


def _task_state_dir_for(run_dir: Path, step_id: str) -> Path:
    """Return the per-task state dir for a non-fan-out step."""
    return run_dir / _STATE_DIR / _TASKS_SUBDIR / step_id


def _write_completed_status(run_dir: Path, step_id: str) -> None:
    """Write a completed status.yaml for a step."""
    state_dir = _task_state_dir_for(run_dir, step_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    record = StatusRecord(
        run_id="test/run1",
        step_id=step_id,
        item={"step": step_id},
        state="completed",
        started_at="2026-04-09T00:00:00",
        completed_at="2026-04-09T00:01:00",
    )
    write_status_at(state_dir, record)


def _write_failed_status(run_dir: Path, step_id: str) -> None:
    """Write a failed status.yaml for a step."""
    state_dir = _task_state_dir_for(run_dir, step_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    record = StatusRecord(
        run_id="test/run1",
        step_id=step_id,
        item={"step": step_id},
        state="failed",
        started_at="2026-04-09T00:00:00",
        completed_at="2026-04-09T00:01:00",
        error="test error",
    )
    write_status_at(state_dir, record)


# ── topo_sort integration ────────────────────────────────────────


class TestTopoSortIntegration:
    """Verify topo_sort levels match expected orchestration order."""

    def test_linear_chain(self) -> None:
        steps = [_step("a"), _step("b", ["a"]), _step("c", ["b"])]
        levels = topo_sort(steps)
        assert levels == [["a"], ["b"], ["c"]]

    def test_diamond(self) -> None:
        steps = [
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["a"]),
            _step("d", ["b", "c"]),
        ]
        levels = topo_sort(steps)
        assert levels == [["a"], ["b", "c"], ["d"]]

    def test_mine_shape(self) -> None:
        """Mine process: fan-out → parallel post-map → join."""
        steps = [
            _step("generate-record"),
            _step("qa-check", ["generate-record"]),
            _step("create-mine-summary", ["generate-record"]),
            _step("generate-usage-report", ["generate-record"]),
            _step(
                "create-mine-overview",
                ["qa-check", "create-mine-summary", "generate-usage-report"],
            ),
        ]
        levels = topo_sort(steps)
        assert levels[0] == ["generate-record"]
        assert set(levels[1]) == {"create-mine-summary", "generate-usage-report", "qa-check"}
        assert levels[2] == ["create-mine-overview"]


# ── Completion detection ──────────────────────────────────────────


class TestCompletionDetection:
    def test_no_status_file(self, tmp_path: Path) -> None:
        assert not _is_step_completed(tmp_path, "step-a")

    def test_completed_step(self, tmp_path: Path) -> None:
        _write_completed_status(tmp_path, "step-a")
        assert _is_step_completed(tmp_path, "step-a")

    def test_completed_step_from_another_run_is_rejected(self, tmp_path: Path) -> None:
        _write_completed_status(tmp_path, "step-a")
        with pytest.raises(ValueError, match="run_id"):
            _is_step_completed(tmp_path, "step-a", expected_run_id="test/run2")

    def test_failed_step_not_complete(self, tmp_path: Path) -> None:
        _write_failed_status(tmp_path, "step-a")
        assert not _is_step_completed(tmp_path, "step-a")

    def test_process_status_fallback_for_fan_out_completion(self, tmp_path: Path) -> None:
        """Fan-out steps don't write a canonical <step>/.state/status.yaml;
        completion is only recorded in process-status.yaml. The fallback
        must recognise these as completed."""
        (tmp_path / STATE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_DIR / "process-status.yaml").write_text(
            textwrap.dedent(
                """\
                process: demo
                started_at: '2026-04-24T00:00:00'
                steps:
                  fan-step:
                    state: completed
                    started_at: '2026-04-24T00:00:00'
                    completed_at: '2026-04-24T00:01:00'
                state: completed
                """
            )
        )
        assert _is_step_completed(tmp_path, "fan-step")

    def test_process_status_fallback_rejects_failed_fan_out(self, tmp_path: Path) -> None:
        """The fallback must NOT accept a fan-out step whose process-status
        entry is ``failed`` (otherwise --from would run downstream over a
        partial upstream)."""
        (tmp_path / STATE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_DIR / "process-status.yaml").write_text(
            textwrap.dedent(
                """\
                process: demo
                started_at: '2026-04-24T00:00:00'
                steps:
                  fan-step:
                    state: failed
                state: failed
                """
            )
        )
        assert not _is_step_completed(tmp_path, "fan-step")

    def test_read_step_status(self, tmp_path: Path) -> None:
        _write_completed_status(tmp_path, "step-a")
        record = _read_step_status(tmp_path, "step-a")
        assert record is not None
        assert record.state == "completed"
        assert record.step_id == "step-a"

    def test_canonical_dir_still_works_with_outputs(self, tmp_path: Path) -> None:
        """Canonical completion is detected when declared outputs still validate."""

        _write_completed_status(tmp_path, "step-a")
        record_path = tmp_path / "other" / "record.md"
        record_path.parent.mkdir()
        record_path.write_text("ok\n")
        outputs = {"record": IOSpec(path=str(record_path))}
        assert _is_step_completed(tmp_path, "step-a", outputs=outputs, variables={})

    def test_completion_revoked_when_declared_output_missing(self, tmp_path: Path) -> None:

        _write_completed_status(tmp_path, "generate-usage-report")
        outputs = {"usage": IOSpec(path="usage.md")}
        variables = {"RUN_ID": "test"}

        assert not _is_step_completed(
            tmp_path,
            "generate-usage-report",
            outputs=outputs,
            variables=variables,
        )

        (tmp_path / "usage.md").write_text("placeholder\n")
        assert _is_step_completed(
            tmp_path,
            "generate-usage-report",
            outputs=outputs,
            variables=variables,
        )

    def test_completion_with_deferred_output_placeholder_skips_output_check(
        self, tmp_path: Path
    ) -> None:

        _write_completed_status(tmp_path, "fan-out-step")
        outputs = {"report": IOSpec(path="{{run.dir}}/reports/{{ticker}}/report.md")}

        assert _is_step_completed(
            tmp_path,
            "fan-out-step",
            outputs=outputs,
            variables={"run.dir": str(tmp_path)},
        )

    def test_fan_out_completion_skips_step_level_output_check(self, tmp_path: Path) -> None:
        (tmp_path / STATE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_DIR / "process-status.yaml").write_text(
            textwrap.dedent(
                """\
                process: demo
                started_at: '2026-04-24T00:00:00'
                steps:
                  predict:
                    state: completed
                state: completed
                """
            )
        )
        outputs = {"report": IOSpec(path="statistical-anchor.md")}

        assert not _is_step_completed(tmp_path, "predict", outputs=outputs, variables={})
        assert _is_step_completed(
            tmp_path,
            "predict",
            outputs=outputs,
            variables={},
            is_fan_out=True,
        )


# ── Recorded step-hash mirror ────────────────────────────────────


class TestRecordedStepHash:
    """Phase 1.2 of plan-2026-05-20-metaproc-step-fingerprint-and-status.md.

    ``_read_recorded_step_hash`` is the single read-side for the step-level
    fingerprint mirror in process-status.yaml, with a fallback to per-task
    result.yaml so old runs that pre-date the mirror still resume cleanly.
    """

    def test_returns_none_when_nothing_on_disk(self, tmp_path: Path) -> None:
        assert _read_recorded_step_hash(tmp_path, "step-a") is None

    def test_reads_from_process_status_mirror(self, tmp_path: Path) -> None:
        (tmp_path / STATE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_DIR / "process-status.yaml").write_text(
            textwrap.dedent(
                """\
                process: demo
                started_at: '2026-05-20T00:00:00'
                steps:
                  step-a:
                    state: completed
                    recorded_step_hash: deadbeef00000001
                state: completed
                """
            )
        )
        assert _read_recorded_step_hash(tmp_path, "step-a") == "deadbeef00000001"

    def test_falls_back_to_per_task_result_yaml(self, tmp_path: Path) -> None:
        """Old runs that completed before the mirror landed have no
        ``recorded_step_hash`` in process-status.yaml — fall through to the
        per-task result.yaml that ``fingerprint_step`` wrote inline."""

        _write_completed_status(tmp_path, "step-a")
        state_dir = _task_state_dir_for(tmp_path, "step-a")
        write_result_at(
            state_dir,
            ResultRecord(
                run_id="test/run1",
                step_id="step-a",
                state="completed",
                validated=True,
                outputs={},
                published_at="2026-05-20T00:00:00",
                step_hash="legacy0000000001",
            ),
        )
        # process-status.yaml absent — fallback path
        assert _read_recorded_step_hash(tmp_path, "step-a") == "legacy0000000001"

    def test_falls_back_when_mirror_lacks_hash(self, tmp_path: Path) -> None:
        """Mirror exists but predates Phase 1.2 (no recorded_step_hash field).
        Per-task result.yaml is the only source of the prior fingerprint."""

        (tmp_path / STATE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_DIR / "process-status.yaml").write_text(
            textwrap.dedent(
                """\
                process: demo
                started_at: '2026-05-20T00:00:00'
                steps:
                  step-a:
                    state: completed
                state: completed
                """
            )
        )
        _write_completed_status(tmp_path, "step-a")
        state_dir = _task_state_dir_for(tmp_path, "step-a")
        write_result_at(
            state_dir,
            ResultRecord(
                run_id="test/run1",
                step_id="step-a",
                state="completed",
                validated=True,
                outputs={},
                published_at="2026-05-20T00:00:00",
                step_hash="legacy0000000002",
            ),
        )
        assert _read_recorded_step_hash(tmp_path, "step-a") == "legacy0000000002"

    def test_returns_none_for_legacy_completion_with_no_fingerprint(self, tmp_path: Path) -> None:
        """Old run that completed before any fingerprinting existed: no
        recorded_step_hash anywhere. Helper returns None so the caller can
        treat the step as a 'legacy completion' rather than a mismatch."""
        _write_completed_status(tmp_path, "step-a")  # no result.yaml written
        assert _read_recorded_step_hash(tmp_path, "step-a") is None


# ── Fingerprint-driven invalidation ──────────────────────────────


class TestFingerprintInvalidation:
    """Phase 1.3 of plan-2026-05-20-metaproc-step-fingerprint-and-status.md.

    Editing a step's prompt/runbook content must invalidate that step
    (so it re-runs) and cascade to downstream steps. Old runs that
    completed before the fingerprint mirror existed must NOT re-execute on
    upgrade — they fall back to 'legacy completion'.
    """

    def _step_with_runbook(self, tmp_path: Path, step_id: str, runbook: Path) -> ResolvedStep:
        return ResolvedStep(
            step_id=step_id,
            mode="agent",
            adapter=ResolvedAdapter(type="test", config={}),
            prompt_paths=[str(runbook)],
            outputs={"out": IOSpec(path=f"{tmp_path}/{step_id}.md")},
        )

    def test_fingerprint_match_keeps_step_completed(self, tmp_path: Path) -> None:
        runbook = tmp_path / "runbook.md"
        runbook.write_text("body v1\n")
        step = self._step_with_runbook(tmp_path, "step-a", runbook)

        _write_completed_status(tmp_path, "step-a")
        (tmp_path / STATE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_DIR / "process-status.yaml").write_text(
            textwrap.dedent(
                f"""\
                process: demo
                started_at: '2026-05-20T00:00:00'
                steps:
                  step-a:
                    state: completed
                    recorded_step_hash: {fingerprint_step(step)}
                state: completed
                """
            )
        )
        assert _is_step_completed(tmp_path, "step-a", step=step) is True

    def test_fingerprint_mismatch_demotes_to_not_completed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        runbook = tmp_path / "runbook.md"
        runbook.write_text("body v1\n")
        step = self._step_with_runbook(tmp_path, "step-a", runbook)

        _write_completed_status(tmp_path, "step-a")
        (tmp_path / STATE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_DIR / "process-status.yaml").write_text(
            textwrap.dedent(
                """\
                process: demo
                started_at: '2026-05-20T00:00:00'
                steps:
                  step-a:
                    state: completed
                    recorded_step_hash: aaaaaaaaaaaaaaaa
                state: completed
                """
            )
        )
        with caplog.at_level("INFO"):
            assert _is_step_completed(tmp_path, "step-a", step=step) is False
        assert "fingerprint changed" in caplog.text
        assert "aaaaaaaaaaaaaaaa" in caplog.text

    def test_legacy_completion_kept_when_no_recorded_hash(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Old run that finished before recorded_step_hash existed must stay
        completed when the operator upgrades and resumes."""
        runbook = tmp_path / "runbook.md"
        runbook.write_text("body\n")
        step = self._step_with_runbook(tmp_path, "step-a", runbook)

        _write_completed_status(tmp_path, "step-a")  # no result.yaml, no mirror
        with caplog.at_level("INFO"):
            assert _is_step_completed(tmp_path, "step-a", step=step) is True
        assert "legacy completion" in caplog.text

    def test_step_param_default_none_preserves_old_behavior(self, tmp_path: Path) -> None:
        """Callers that don't pass ``step=`` get the pre-Phase-1.3 verdict
        (fingerprint ignored). Keeps legacy tests and ad-hoc callers stable."""
        _write_completed_status(tmp_path, "step-a")
        assert _is_step_completed(tmp_path, "step-a") is True

    def test_cascade_renames_descendants_status_yaml(self, tmp_path: Path) -> None:
        runbook = tmp_path / "rb.md"
        runbook.write_text("v1\n")
        step_a = self._step_with_runbook(tmp_path, "a", runbook)
        step_b = ResolvedStep(
            step_id="b",
            mode="code",
            adapter=ResolvedAdapter(type="test", config={}),
            needs=["a"],
        )
        step_c = ResolvedStep(
            step_id="c",
            mode="code",
            adapter=ResolvedAdapter(type="test", config={}),
            needs=["b"],
        )
        plan = _make_plan(step_a, step_b, step_c)

        _write_completed_status(tmp_path, "a")
        _write_completed_status(tmp_path, "b")
        _write_completed_status(tmp_path, "c")
        (tmp_path / STATE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_DIR / "process-status.yaml").write_text(
            textwrap.dedent(
                """\
                process: demo
                started_at: '2026-05-20T00:00:00'
                steps:
                  a:
                    state: completed
                    recorded_step_hash: stalehash00000001
                state: completed
                """
            )
        )

        out = FakeOut()
        invalidated = _maybe_cascade_for_fingerprint(
            tmp_path, plan, step_a, variables=None, out=out
        )
        assert set(invalidated) == {"a", "b", "c"}
        for step_id in ("a", "b", "c"):
            sd = _task_state_dir_for(tmp_path, step_id)
            assert not (sd / STATUS_FILE).exists()
            assert (sd / "status.yaml.stale").exists()

    def test_cascade_no_op_when_fingerprint_matches(self, tmp_path: Path) -> None:
        runbook = tmp_path / "rb.md"
        runbook.write_text("v1\n")
        step_a = self._step_with_runbook(tmp_path, "a", runbook)
        plan = _make_plan(step_a)

        _write_completed_status(tmp_path, "a")
        (tmp_path / STATE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / STATE_DIR / "process-status.yaml").write_text(
            textwrap.dedent(
                f"""\
                process: demo
                started_at: '2026-05-20T00:00:00'
                steps:
                  a:
                    state: completed
                    recorded_step_hash: {fingerprint_step(step_a)}
                state: completed
                """
            )
        )
        out = FakeOut()
        invalidated = _maybe_cascade_for_fingerprint(
            tmp_path, plan, step_a, variables=None, out=out
        )
        assert invalidated == []
        assert (_task_state_dir_for(tmp_path, "a") / STATUS_FILE).exists()

    def test_cascade_no_op_for_legacy_completion(self, tmp_path: Path) -> None:
        """When no fingerprint was recorded, the cascade does not fire — old
        runs upgrading to Phase 1.3 must not auto-rerun on resume."""
        runbook = tmp_path / "rb.md"
        runbook.write_text("v1\n")
        step_a = self._step_with_runbook(tmp_path, "a", runbook)
        plan = _make_plan(step_a)

        _write_completed_status(tmp_path, "a")  # no recorded hash anywhere
        out = FakeOut()
        invalidated = _maybe_cascade_for_fingerprint(
            tmp_path, plan, step_a, variables=None, out=out
        )
        assert invalidated == []


# ── Downstream invalidation ──────────────────────────────────────


class TestDownstreamInvalidation:
    def test_invalidate_linear(self, tmp_path: Path) -> None:
        plan = _make_plan(
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["b"]),
        )
        _write_completed_status(tmp_path, "a")
        _write_completed_status(tmp_path, "b")
        _write_completed_status(tmp_path, "c")

        invalidated = _invalidate_downstream(tmp_path, "a", plan)
        assert set(invalidated) == {"a", "b", "c"}

        # status.yaml at the per-task state dir is renamed to .stale.
        a_state = _task_state_dir_for(tmp_path, "a")
        assert not (a_state / STATUS_FILE).exists()
        assert (a_state / "status.yaml.stale").exists()

    def test_invalidate_partial(self, tmp_path: Path) -> None:
        """Only invalidate steps that have status files."""
        plan = _make_plan(
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["b"]),
        )
        _write_completed_status(tmp_path, "a")
        # b and c have no status files

        invalidated = _invalidate_downstream(tmp_path, "a", plan)
        assert invalidated == ["a"]

    def test_invalidate_diamond(self, tmp_path: Path) -> None:
        plan = _make_plan(
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["a"]),
            _step("d", ["b", "c"]),
        )
        _write_completed_status(tmp_path, "b")
        _write_completed_status(tmp_path, "c")
        _write_completed_status(tmp_path, "d")

        invalidated = _invalidate_downstream(tmp_path, "b", plan)
        # b depends on nothing, d depends on b
        assert "b" in invalidated
        assert "d" in invalidated
        assert "c" not in invalidated  # c doesn't depend on b

    def test_invalidate_at_canonical_task_state(self, tmp_path: Path) -> None:
        """Invalidation renames status.yaml at the per-task state location.

        Completion state lives at ``<run>/.state/tasks/<step_id>/status.yaml``
        regardless of where output artifacts are templated to land.
        """
        run_dir = tmp_path / "run-1" / "mine"
        run_dir.mkdir(parents=True)
        plan = _make_plan(
            _step("generate-record"),
            ResolvedStep(
                step_id="qa-check",
                mode="code",
                adapter=ResolvedAdapter(type="test", config={}),
                needs=["generate-record"],
                outputs={
                    "qa_report": IOSpec(
                        path="{{run.parent_dir}}/{{run.id}}/mine/qa/qa-summary.md",
                    )
                },
            ),
        )
        variables = {"RUNS_DIR": str(tmp_path), "RUN_ID": "run-1"}
        qa_state = _task_state_dir_for(run_dir, "qa-check")
        qa_state.mkdir(parents=True)
        record = StatusRecord(
            run_id="test/run1",
            step_id="qa-check",
            item={"step": "qa-check"},
            state="completed",
            started_at="2026-04-09T00:00:00",
            completed_at="2026-04-09T00:01:00",
        )
        write_status_at(qa_state, record)

        invalidated = _invalidate_downstream(run_dir, "generate-record", plan, variables=variables)

        assert "qa-check" in invalidated
        assert not (qa_state / STATUS_FILE).exists()
        assert (qa_state / "status.yaml.stale").exists()
        outputs = plan.steps[1].outputs
        assert not _is_step_completed(run_dir, "qa-check", outputs=outputs, variables=variables)


# ── Ancestor verification ────────────────────────────────────────


class TestAncestorVerification:
    def test_all_ancestors_completed(self, tmp_path: Path) -> None:
        plan = _make_plan(
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["b"]),
        )
        spec = _make_spec("a", "b", "c")
        _write_completed_status(tmp_path, "a")
        # Running only b, c — a is omitted and completed
        errors = _verify_ancestors(tmp_path, plan, {"b", "c"}, spec, {})
        assert errors == []

    def test_missing_ancestor(self, tmp_path: Path) -> None:
        plan = _make_plan(
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["b"]),
        )
        spec = _make_spec("a", "b", "c")
        # a has no status file
        errors = _verify_ancestors(tmp_path, plan, {"b", "c"}, spec, {})
        assert len(errors) == 1
        assert "a" in errors[0]
        assert "no completion record" in errors[0]

    def test_failed_ancestor(self, tmp_path: Path) -> None:
        plan = _make_plan(
            _step("a"),
            _step("b", ["a"]),
            _step("c", ["b"]),
        )
        spec = _make_spec("a", "b", "c")
        _write_failed_status(tmp_path, "a")
        errors = _verify_ancestors(tmp_path, plan, {"b", "c"}, spec, {})
        assert len(errors) == 1
        assert "failed" in errors[0]


# ── Process status file ──────────────────────────────────────────


class TestProcessStatusFile:
    @pytest.mark.parametrize(
        ("suffix", "extra_args", "expected_publish_state"),
        [
            ("only", ["--only", "intake", "--no-continue-on-error"], None),
            ("full", [], "blocked"),
        ],
    )
    def test_code_handler_failure_is_projected_to_run_status_and_events(
        self,
        tmp_path: Path,
        suffix: str,
        extra_args: list[str],
        expected_publish_state: str | None,
    ) -> None:
        process_dir = tmp_path / "failing-process"
        process_dir.mkdir()
        (process_dir / "handlers.py").write_text(
            "def fail(_context, _step):\n    raise RuntimeError('source attestation mismatch')\n",
            encoding="utf-8",
        )
        process_path = process_dir / "test.process.md"
        process_path.write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: failing-process
                  steps:
                    - id: intake
                      mode: code
                      handler: handlers.py:fail
                    - id: publish
                      mode: code
                      command: "true"
                      needs: [intake]
                ---
                """
            ),
            encoding="utf-8",
        )
        runs_dir = tmp_path / "runs"
        run_id = f"failed-code-handler-{suffix}"

        result = CliRunner().invoke(
            app,
            [
                "run-process",
                str(process_path),
                "--var",
                f"RUNS_DIR={runs_dir}",
                "--var",
                f"RUN_ID={run_id}",
                *extra_args,
            ],
        )

        assert result.exit_code == 1
        expected_error = "RuntimeError: source attestation mismatch"
        assert expected_error in result.output
        assert result.exception is not None
        assert expected_error in str(result.exception)

        run_dir = runs_dir / run_id
        process_status = read_yaml_file(run_dir / STATE_DIR / "process-status.yaml")
        assert process_status["state"] == "failed"
        assert expected_error in process_status["steps"]["intake"]["error"]
        if expected_publish_state is None:
            assert "publish" not in process_status["steps"]
        else:
            assert process_status["steps"]["publish"]["state"] == expected_publish_state

        events = [
            json.loads(line)
            for line in (run_dir / LOGS_DIR / "process-events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        failure = next(event for event in events if event["event"] == "step_fail")
        assert expected_error in failure["error"]

        status = CliRunner().invoke(app, ["status", str(run_dir)])
        assert status.exit_code == 0, status.output
        assert "Status: FAILED" in status.output
        assert expected_error in status.output
        assert "Process: current" not in status.output

    def test_orchestrator_reconciles_tasks_before_topology_walk(self, tmp_path: Path) -> None:
        out = FakeOut()
        reconcile = MagicMock(return_value=2)

        with (
            _test_execution_context() as execution_context,
            patch("metaproc.commands.run_process.reconcile_stale_running", reconcile),
            patch(
                "metaproc.commands.run_process.topo_sort",
                side_effect=RuntimeError("stop after reconciliation"),
            ),
            pytest.raises(RuntimeError, match="stop after reconciliation"),
        ):
            asyncio.run(
                _orchestrate(
                    spec=ProcessSpec(name="test"),
                    plan=_make_plan(),
                    variables={},
                    process_path=tmp_path / "test.process.md",
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run",
                    execution_context=execution_context,
                    out=out,
                    events=MagicMock(),
                )
            )

        reconcile.assert_called_once_with(tmp_path / "run")
        assert out.messages == ["Reconciled 2 orphaned task(s)"]

    def test_write_running_status(self, tmp_path: Path) -> None:
        step_states = {
            "step-a": {"state": "completed"},
            "step-b": {"state": "running"},
            "step-c": {"state": "pending"},
        }
        path = _write_process_status(tmp_path, "test-process", step_states, "2026-04-09T00:00:00")
        assert path.exists()
        content = path.read_text()
        assert "test-process" in content
        assert "running" in content

    def test_all_completed_status(self, tmp_path: Path) -> None:
        step_states = {
            "step-a": {"state": "completed"},
            "step-b": {"state": "completed"},
        }
        path = _write_process_status(tmp_path, "test", step_states, "2026-04-09T00:00:00")
        content = path.read_text()
        assert "state: completed" in content

    def test_failed_overall_status(self, tmp_path: Path) -> None:
        step_states = {
            "step-a": {"state": "completed"},
            "step-b": {"state": "failed"},
        }
        path = _write_process_status(tmp_path, "test", step_states, "2026-04-09T00:00:00")
        content = path.read_text()
        assert "state: failed" in content

    @pytest.mark.parametrize(
        ("other_state", "expected"),
        [("running", "running"), ("failed", "failed")],
    )
    def test_current_work_outranks_carried_cancelled_state(
        self,
        tmp_path: Path,
        other_state: str,
        expected: str,
    ) -> None:
        path = _write_process_status(
            tmp_path,
            "test",
            {
                "prior-step": {"state": "cancelled"},
                "active-step": {"state": other_state},
            },
            "2026-04-09T00:00:00",
        )

        assert read_yaml_file(path)["state"] == expected

    def test_partial_success_ignores_carried_cancelled_state(self, tmp_path: Path) -> None:
        path = _write_process_status(
            tmp_path,
            "test",
            {
                "prior-step": {"state": "cancelled"},
                "active-step": {"state": "completed"},
            },
            "2026-04-09T00:00:00",
            active_step_ids={"active-step"},
        )

        assert read_yaml_file(path)["state"] == "completed"

    def test_partial_cancellation_uses_the_active_step_state(self, tmp_path: Path) -> None:
        path = _write_process_status(
            tmp_path,
            "test",
            {
                "prior-step": {"state": "completed"},
                "active-step": {"state": "cancelled"},
            },
            "2026-04-09T00:00:00",
            active_step_ids={"active-step"},
        )

        assert read_yaml_file(path)["state"] == "cancelled"


# ── Fan-out execution ────────────────────────────────────────────


class TestFanOutExecution:
    @pytest.mark.parametrize(
        (
            "adapter_type",
            "pool_root_name",
            "expected_success",
            "expected_scope",
            "warning_fragment",
        ),
        [
            ("claude-code-cli", "runs", True, "2026-08-24/research/AAPL", None),
            ("pi-cli", "runs", True, None, "pool is configured for 'claude-code-cli'"),
            (
                "claude-code-cli",
                "other-runs",
                False,
                None,
                "outside credential pool runs directory",
            ),
        ],
    )
    def test_binds_matching_fan_out_pool_and_warns_on_mismatch(
        self,
        tmp_path: Path,
        adapter_type: str,
        pool_root_name: str,
        expected_success: bool,
        expected_scope: str | None,
        warning_fragment: str | None,
    ) -> None:
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "2026-08-24" / "research" / "AAPL"
        source_path = tmp_path / "tickers.md"
        source_path.write_text("---\nitems: []\n---\n")
        step_def = ProcessStep(
            id="predict",
            mode="agent",
            for_each=ForEach(
                over="deps.tickers",
                bind="ticker",
                bind_fields=["ticker"],
                key="{{ticker}}",
            ),
        )
        target = ResolvedStep(
            step_id="predict",
            mode="agent",
            adapter=ResolvedAdapter(type=adapter_type, config={}),
            fan_out=FanOut(
                over="deps.tickers",
                bind="ticker",
                source=str(source_path),
                bind_fields=["ticker"],
            ),
        )
        discovery = FanOutDiscovery(
            source_path=source_path,
            item_key="ticker",
            item_fields=["ticker"],
            actionable_contexts=[{"ticker": "AAPL"}],
        )
        template = PoolDispatchConfig(
            coordinator=MagicMock(),
            adapter="claude-code-cli",
            runs_dir=tmp_path / pool_root_name,
            run_id="2026-08-24",
            step="",
        )
        run_pool = AsyncMock(return_value=[("AAPL", 0)])
        out = FakeOut()

        with (
            patch("metaproc.commands.run_process.derive_variant", return_value="test"),
            patch(
                "metaproc.commands.run_process.discover_items_from_source",
                return_value=discovery,
            ),
            patch("metaproc.commands.run_process.reconcile_stale_running", return_value=0),
            patch("metaproc.commands.run_process.run_preflight", return_value=[]),
            patch("metaproc.commands.run_process.get_backend", return_value=MagicMock()),
            patch("metaproc.commands.run_process.capture_repo_snapshot", return_value=None),
            patch("metaproc.commands.run_parallel._run_agent_pool", new=run_pool),
        ):
            succeeded = asyncio.run(
                _execute_fan_out_step(
                    spec=ProcessSpec(name="gtia-v30-pre"),
                    step_def=step_def,
                    target=target,
                    variables={},
                    process_path=tmp_path / "test.process.md",
                    process_dir=tmp_path,
                    run_dir=run_dir,
                    run_id="gtia-v30-pre/2026-08-24/research/AAPL",
                    backend_name="local",
                    max_concurrency=1,
                    num_workers=1,
                    machine_type="e2-standard-4",
                    spot=False,
                    variant_override=None,
                    out=out,
                    pool_dispatch_template=template,
                )
            )

        assert succeeded is expected_success
        call = run_pool.await_args
        if expected_success:
            assert call is not None
            bound = call.kwargs["pool_dispatch"]
            if expected_scope is None:
                assert bound is None
            else:
                assert bound.run_id == expected_scope
                assert (bound.runs_dir / bound.run_id).resolve() == run_dir.resolve()
        else:
            assert call is None
        if warning_fragment is None:
            assert out.warnings == []
        else:
            assert any(warning_fragment in warning for warning in out.warnings)

    def test_write_boundary_still_runs_when_another_item_failed(self, tmp_path: Path) -> None:
        source_path = tmp_path / "tickers.md"
        source_path.write_text("---\nitems: []\n---\n")
        run_dir = tmp_path / "run"
        step_def = ProcessStep(
            id="predict",
            mode="agent",
            for_each=ForEach(
                over="deps.tickers",
                bind="ticker",
                bind_fields=["ticker"],
                key="{{ticker}}",
            ),
        )
        target = ResolvedStep(
            step_id="predict",
            mode="agent",
            adapter=ResolvedAdapter(type="claude-code-cli", config={}),
            outputs={"report": IOSpec(path=str(run_dir / "reports" / "{{ticker}}.md"))},
            fan_out=FanOut(
                over="deps.tickers",
                bind="ticker",
                source=str(source_path),
                bind_fields=["ticker"],
            ),
        )
        item_contexts = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
        discovery = FanOutDiscovery(
            source_path=source_path,
            item_key="ticker",
            item_fields=["ticker"],
            actionable_contexts=item_contexts,
        )
        snapshot = RepoSnapshot(repo_root=tmp_path, statuses={}, dirty_stats={})
        pool_results = [("AAPL", 1), ("MSFT", 0)]
        finalize = MagicMock(return_value=pool_results)

        with (
            patch("metaproc.commands.run_process.derive_variant", return_value="test"),
            patch(
                "metaproc.commands.run_process.discover_items_from_source",
                return_value=discovery,
            ),
            patch("metaproc.commands.run_process.reconcile_stale_running", return_value=0),
            patch("metaproc.commands.run_process.run_preflight", return_value=[]),
            patch("metaproc.commands.run_process.get_backend", return_value=MagicMock()),
            patch(
                "metaproc.commands.run_process.collect_write_targets",
                return_value=[WriteTarget(run_dir / "reports", "tree")],
            ),
            patch(
                "metaproc.commands.run_process.capture_repo_snapshot",
                side_effect=[snapshot, snapshot],
            ),
            patch(
                "metaproc.commands.run_process.repo_changes_since",
                return_value=[tmp_path / "stray.md"],
            ),
            patch(
                "metaproc.commands.run_process.filter_boundary_violations",
                return_value=[tmp_path / "stray.md"],
            ),
            patch(
                "metaproc.commands.run_parallel._run_agent_pool",
                new=AsyncMock(return_value=pool_results),
            ),
            patch(
                "metaproc.commands.run_process._finish_deferred_fan_out_attempts",
                finalize,
            ),
        ):
            result = asyncio.run(
                _execute_fan_out_step(
                    spec=ProcessSpec(name="test"),
                    step_def=step_def,
                    target=target,
                    variables={},
                    process_path=tmp_path / "test.process.md",
                    process_dir=tmp_path,
                    run_dir=run_dir,
                    run_id="test/run",
                    backend_name="local",
                    max_concurrency=None,
                    num_workers=1,
                    machine_type="e2-standard-4",
                    spot=False,
                    variant_override=None,
                    out=FakeOut(),
                )
            )

        assert result is False
        boundary_error = finalize.call_args.kwargs["boundary_error"]
        assert boundary_error is not None
        assert "stray.md" in boundary_error

    def test_uses_resolved_fan_out_source(self, tmp_path: Path) -> None:
        source_path = tmp_path / "tickers.md"
        source_path.write_text("---\nitems: []\n---\n")
        step_def = ProcessStep(
            id="generate-record",
            mode="agent",
            for_each=ForEach.model_validate(
                {"over": "deps.items", "bind": "event_id", "bind_fields": ["event_id"]}
            ),
        )
        target = ResolvedStep(
            step_id="generate-record",
            mode="agent",
            adapter=ResolvedAdapter(type="claude-code-cli", config={}),
            fan_out=FanOut(
                over="deps.items",
                bind="event_id",
                source=str(source_path),
                bind_fields=["event_id"],
            ),
        )
        out = FakeOut()

        discovery = FanOutDiscovery(
            source_path=source_path,
            item_key="event_id",
            item_fields=["event_id"],
        )

        with (
            patch("metaproc.adapters.registry.derive_variant", return_value="test-variant"),
            patch(
                "metaproc.commands.run_process.discover_items_from_source", return_value=discovery
            ),
            patch("metaproc.commands.run_process.reconcile_stale_running", return_value=0),
        ):
            result = asyncio.run(
                _execute_fan_out_step(
                    spec=ProcessSpec(name="test"),
                    step_def=step_def,
                    target=target,
                    variables={},
                    process_path=tmp_path / "test.process.md",
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="run/test",
                    backend_name="local",
                    max_concurrency=None,
                    num_workers=1,
                    machine_type="e2-standard-4",
                    spot=False,
                    variant_override=None,
                    out=out,
                )
            )

        assert result is True
        assert out.messages == ["  Step 'generate-record': no actionable items (all completed)"]


# ── Composite step execution ────────────────────────────────────


class TestCompositeStepExecution:
    def test_composite_loads_child_spec(self, tmp_path: Path) -> None:
        """Composite step resolves uses path and loads child spec."""

        # Create child process.md
        child_dir = tmp_path / "child"
        child_dir.mkdir()
        child_spec_content = textwrap.dedent("""\
            ---
            type: process
            process:
              name: child-test
              steps:
                - id: noop
                  mode: code
                  command: "true"
            ---
            Child process for testing.
        """)
        (child_dir / "test.process.md").write_text(child_spec_content)

        # Create a step_def that uses the child dep.
        step_def = ProcessStep(id="preflight", mode="composite", uses="deps.child_process")
        target = ResolvedStep(
            step_id="preflight",
            mode="composite",
            uses_path=str(child_dir / "test.process.md"),
        )

        with _test_execution_context() as execution_context:
            result = asyncio.run(
                _execute_composite_step(
                    step_def=step_def,
                    target=target,
                    variables={"RUN_ID": "test-run"},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run",
                    scope_path=(),
                    execution_context=execution_context,
                    out=FakeOut(),
                )
            )
        assert result is True

        # Child run dir should be scoped under parent
        assert (tmp_path / "run" / "preflight").exists()

    def test_composite_propagates_variant_override_to_child_plan(self, tmp_path: Path) -> None:
        """Composite child plans must honor the top-level adapter override."""

        child_dir = tmp_path / "child"
        child_dir.mkdir()
        child_spec_content = textwrap.dedent("""\
            ---
            process:
              name: child-test
              defaults:
                default_adapter: claude-code-cli
                adapters:
                  claude-code-cli:
                    type: claude-code-cli
                    config:
                      permission_mode: bypassPermissions
                      output_format: stream-json
                      verbose: true
                      no_session_persistence: true
                  codex-cli:
                    type: codex-cli
                    config:
                      permission_mode: bypassPermissions
                      model: gpt-5.5
                      effort: high
              steps:
                - id: child-agent
                  mode: agent
                  prompt_prefix: "do the thing"
                  output_root: child-agent
                  adapter:
                    config:
                      permission_mode: bypassPermissions
                      tools: [Read, Write]
                  outputs:
                    child_output:
                      path: out.md
                      kind: file
            ---
            Child process for testing.
        """)
        (child_dir / "test.process.md").write_text(child_spec_content)

        step_def = ProcessStep(id="preflight", mode="composite", uses="deps.child_process")
        target = ResolvedStep(
            step_id="preflight",
            mode="composite",
            uses_path=str(child_dir / "test.process.md"),
        )

        captured: dict[str, Plan] = {}

        async def fake_orchestrate(**kwargs: object) -> None:
            captured["plan"] = cast(Plan, kwargs["plan"])

        with (
            _test_execution_context(variant_override="codex-cli") as execution_context,
            patch("metaproc.commands.run_process._orchestrate", fake_orchestrate),
        ):
            result = asyncio.run(
                _execute_composite_step(
                    step_def=step_def,
                    target=target,
                    variables={"RUN_ID": "test-run"},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run",
                    scope_path=(),
                    execution_context=execution_context,
                    out=FakeOut(),
                )
            )

        assert result is True
        assert captured["plan"].steps[0].adapter.type == "codex-cli"
        assert "output_format" not in captured["plan"].steps[0].adapter.config

    def test_composite_propagates_profile_files_to_child_plan(self, tmp_path: Path) -> None:
        """Composite child plans must resolve execution profiles from CLI profile files."""

        profile_file = tmp_path / "profiles.yaml"
        profile_file.write_text(
            textwrap.dedent("""\
                profiles:
                  local-codex:
                    schema: metaproc:ExecutionProfile/0.1
                    adapter: codex-cli
                    provider: openai
                    model: gpt-5.5
                    resources:
                      max_concurrency_hint: 4
                      host_max_concurrency: 4
                    config:
                      model: gpt-5.5
                      effort: high
            """),
            encoding="utf-8",
        )

        child_dir = tmp_path / "child"
        child_dir.mkdir()
        child_spec_content = textwrap.dedent("""\
            ---
            process:
              name: child-test
              defaults:
                adapters:
                  codex-cli:
                    type: codex-cli
                    config:
                      permission_mode: bypassPermissions
              steps:
                - id: child-agent
                  mode: agent
                  prompt_prefix: "do the thing"
                  output_root: child-agent
                  outputs:
                    child_output:
                      path: out.md
                      kind: file
            ---
            Child process for testing.
        """)
        (child_dir / "test.process.md").write_text(child_spec_content, encoding="utf-8")

        step_def = ProcessStep(id="preflight", mode="composite", uses="deps.child_process")
        target = ResolvedStep(
            step_id="preflight",
            mode="composite",
            uses_path=str(child_dir / "test.process.md"),
        )

        captured: dict[str, Plan] = {}

        async def fake_orchestrate(**kwargs: object) -> None:
            captured["plan"] = cast(Plan, kwargs["plan"])

        with (
            _test_execution_context(
                variant_override="local-codex",
                profile_files=[profile_file],
            ) as execution_context,
            patch("metaproc.commands.run_process._orchestrate", fake_orchestrate),
        ):
            result = asyncio.run(
                _execute_composite_step(
                    step_def=step_def,
                    target=target,
                    variables={"RUN_ID": "test-run"},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run",
                    scope_path=(),
                    execution_context=execution_context,
                    out=FakeOut(),
                )
            )

        assert result is True
        assert captured["plan"].execution_profile == "local-codex"
        assert captured["plan"].steps[0].adapter.type == "codex-cli"
        assert captured["plan"].steps[0].adapter.config["model"] == "gpt-5.5"

    def test_composite_opens_child_process_event_log(self, tmp_path: Path) -> None:

        child_dir = tmp_path / "child"
        child_dir.mkdir()
        child_spec_content = textwrap.dedent("""\
            ---
            process:
              name: child-test
              steps:
                - id: noop
                  mode: code
                  command: "true"
            ---
            Child process for testing.
        """)
        (child_dir / "test.process.md").write_text(child_spec_content)
        step_def = ProcessStep(id="preflight", mode="composite", uses="deps.child_process")
        target = ResolvedStep(
            step_id="preflight",
            mode="composite",
            uses_path=str(child_dir / "test.process.md"),
        )

        async def fake_orchestrate(**kwargs: object) -> None:
            events = cast(ProcessEventLogger, kwargs["events"])
            events.process_start("child-test", "test/run/preflight", "local", 1)

        with (
            _test_execution_context() as execution_context,
            patch("metaproc.commands.run_process._orchestrate", fake_orchestrate),
        ):
            result = asyncio.run(
                _execute_composite_step(
                    step_def=step_def,
                    target=target,
                    variables={"RUN_ID": "test-run"},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run",
                    scope_path=(),
                    execution_context=execution_context,
                    out=FakeOut(),
                )
            )

        assert result is True
        event_log = tmp_path / "run" / "preflight" / LOGS_DIR / "process-events.jsonl"
        assert "process_start" in event_log.read_text(encoding="utf-8")

    def test_composite_missing_uses_raises(self, tmp_path: Path) -> None:
        """Composite step without uses field raises CLIError."""

        step_def = ProcessStep(id="bad", mode="composite", uses="deps.child_process")
        target = ResolvedStep(step_id="bad", mode="composite")

        with (
            _test_execution_context() as execution_context,
            pytest.raises(CLIError, match="requires a resolved child process"),
        ):
            asyncio.run(
                _execute_composite_step(
                    step_def=step_def,
                    target=target,
                    variables={},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="test/run",
                    scope_path=(),
                    execution_context=execution_context,
                    out=FakeOut(),
                )
            )

    def test_composite_validates_declared_child_process_outputs(self, tmp_path: Path) -> None:
        """A composite succeeds only after its child's declared output ports validate."""

        child_spec_path = tmp_path / "child.process.md"
        child_spec_path.write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: child
                  outputs:
                    report:
                      path: "{{run.dir}}/missing.md"
                      as: path
                  steps:
                    - id: noop
                      mode: code
                      command: "true"
                ---
                Child process with an intentionally missing declared output.
                """
            ),
            encoding="utf-8",
        )
        step_def = ProcessStep(id="child", mode="composite", uses="deps.child")
        target = ResolvedStep(
            step_id="child",
            mode="composite",
            uses_path=str(child_spec_path),
        )
        out = FakeOut()

        with _test_execution_context() as execution_context:
            result = asyncio.run(
                _execute_composite_step(
                    step_def=step_def,
                    target=target,
                    variables={"RUNS_DIR": str(tmp_path), "RUN_ID": "run"},
                    process_dir=tmp_path,
                    run_dir=tmp_path / "run",
                    run_id="run",
                    scope_path=(),
                    execution_context=execution_context,
                    out=out,
                )
            )

        assert result is False
        assert any("child process output validation failed" in message for message in out.messages)

    def test_mapped_composite_isolates_failure_and_resumes_only_failed_item(
        self,
        tmp_path: Path,
    ) -> None:
        """One parent run maps child scopes; resume retains successful siblings."""

        process_dir = tmp_path / "process"
        process_dir.mkdir()
        roster_path = process_dir / "roster.md"
        roster_path.write_text(
            textwrap.dedent(
                """\
                ---
                progress:
                  schema: metaproc:ProgressSpec/0.1
                  process: mapped-composite-smoke
                  items:
                    - ticker: alfa
                      should_fail: false
                    - ticker: brvo
                      should_fail: true
                    - ticker: chrl
                      should_fail: false
                ---
                Three synthetic items.
                """
            ),
            encoding="utf-8",
        )
        fail_marker = tmp_path / "fail-brvo"
        fail_marker.touch()
        (process_dir / "child.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: ticker-child
                  inputs:
                    ticker: { param: TICKER, as: string }
                    should_fail: { param: SHOULD_FAIL, as: string }
                    fail_marker: { param: FAIL_MARKER, as: path }
                  outputs:
                    report:
                      path: "{{run.dir}}/report.txt"
                      as: path
                  steps:
                    - id: write-report
                      mode: code
                      command: >-
                        /bin/sh -c 'if [ "{{SHOULD_FAIL}}" = "true" ] && [ -f "{{FAIL_MARKER}}" ]; then exit 7; fi;
                        mkdir -p "{{run.dir}}";
                        printf "%s\\n" "{{TICKER}}" > "{{run.dir}}/report.txt"'
                ---
                One deterministic child scope per item.
                """
            ),
            encoding="utf-8",
        )
        (process_dir / "parent.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: mapped-composite-smoke
                  inputs:
                    fail_marker: { param: FAIL_MARKER, as: path }
                  deps:
                    roster:
                      path: ./roster.md
                      as: path
                    child:
                      path: ./child.process.md
                      as: path
                  steps:
                    - id: ticker-flow
                      mode: composite
                      uses: deps.child
                      for_each:
                        over: deps.roster
                        bind: ticker
                        bind_fields: [ticker, should_fail]
                        key: "{{ticker}}"
                        max_concurrency: 3
                      with:
                        TICKER: "{{ticker}}"
                        SHOULD_FAIL: "{{should_fail}}"
                        FAIL_MARKER: "{{FAIL_MARKER}}"
                      outputs:
                        report:
                          path: "{{run.dir}}/ticker-flow/{{ticker}}/report.txt"
                          kind: file
                ---
                Minimal mapped-composite parent.
                """
            ),
            encoding="utf-8",
        )

        runs_dir = tmp_path / "runs"
        args = [
            "run-process",
            str(process_dir / "parent.process.md"),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            "RUN_ID=gtia-v30pre-2026-08-24-m0",
            "--var",
            f"FAIL_MARKER={fail_marker}",
            "--max-concurrency",
            "2",
        ]
        runner = CliRunner()

        first = runner.invoke(app, args)
        assert first.exit_code != 0
        run_dir = runs_dir / "gtia-v30pre-2026-08-24-m0"
        state_root = run_dir / STATE_DIR / "tasks" / "ticker-flow"
        alfa_status = read_status_at(state_root / "alfa")
        brvo_status = read_status_at(state_root / "brvo")
        chrl_status = read_status_at(state_root / "chrl")
        assert alfa_status is not None and alfa_status.state == "completed"
        assert brvo_status is not None and brvo_status.state == "failed"
        assert chrl_status is not None and chrl_status.state == "completed"
        assert (run_dir / "ticker-flow" / "alfa" / STATE_DIR / "process-status.yaml").exists()
        assert (run_dir / "ticker-flow" / "brvo" / STATE_DIR / "process-status.yaml").exists()
        assert (run_dir / "ticker-flow" / "chrl" / STATE_DIR / "process-status.yaml").exists()
        assert not list((run_dir / "ticker-flow").rglob(ORCHESTRATOR_LEASE_FILE))
        process_events_path = run_dir / LOGS_DIR / "process-events.jsonl"
        first_events = [
            json.loads(line)
            for line in process_events_path.read_text(encoding="utf-8").splitlines()
        ]
        assert sorted(
            event["item_key"] for event in first_events if event["event"] == "item_start"
        ) == ["alfa", "brvo", "chrl"]
        assert sorted(
            event["item_key"] for event in first_events if event["event"] == "item_complete"
        ) == ["alfa", "chrl"]
        assert [event["item_key"] for event in first_events if event["event"] == "item_fail"] == [
            "brvo"
        ]

        fail_marker.unlink()
        second = runner.invoke(app, args)
        assert second.exit_code == 0, second.output
        assert [record.disposition for record in read_attempt_history_at(state_root / "alfa")] == [
            AttemptDisposition.succeeded
        ]
        assert [record.disposition for record in read_attempt_history_at(state_root / "brvo")] == [
            AttemptDisposition.permanent,
            AttemptDisposition.succeeded,
        ]
        assert [record.disposition for record in read_attempt_history_at(state_root / "chrl")] == [
            AttemptDisposition.succeeded
        ]
        assert {
            child_dir.name: (child_dir / "report.txt").read_text(encoding="utf-8").strip()
            for child_dir in (run_dir / "ticker-flow").iterdir()
            if child_dir.is_dir()
        } == {"alfa": "alfa", "brvo": "brvo", "chrl": "chrl"}
        all_events = [
            json.loads(line)
            for line in process_events_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [event["item_key"] for event in all_events if event["event"] == "item_start"].count(
            "brvo"
        ) == 2
        assert [
            event["item_key"] for event in all_events if event["event"] == "item_complete"
        ].count("brvo") == 1

    def test_mapped_scalar_agents_share_one_real_run_pool(self, tmp_path: Path) -> None:
        process_dir = tmp_path / "process"
        process_dir.mkdir()
        (process_dir / "roster.md").write_text(
            "---\nprogress:\n  items:\n    - ticker: alfa\n    - ticker: brvo\n---\n",
            encoding="utf-8",
        )
        (process_dir / "child.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: agent-child
                  inputs:
                    ticker: { param: TICKER, as: string }
                  outputs:
                    analysis: { path: "{{run.dir}}/analysis.txt", as: path }
                  steps:
                    - id: analyze
                      mode: agent
                      prompt_prefix: "Analyze {{TICKER}}."
                      outputs:
                        analysis: { path: "{{run.dir}}/analysis.txt", kind: file }
                ---
                One scalar agent leaf.
                """
            ),
            encoding="utf-8",
        )
        (process_dir / "parent.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: mapped-agent-pool-smoke
                  deps:
                    roster: { path: ./roster.md, as: path }
                    child: { path: ./child.process.md, as: path }
                  steps:
                    - id: ticker-flow
                      mode: composite
                      uses: deps.child
                      for_each:
                        over: deps.roster
                        bind: ticker
                        bind_fields: [ticker]
                        key: "{{ticker}}"
                      with:
                        TICKER: "{{ticker}}"
                      outputs:
                        analysis:
                          path: "{{run.dir}}/ticker-flow/{{ticker}}/analysis.txt"
                          kind: file
                ---
                Two mapped scalar leaves.
                """
            ),
            encoding="utf-8",
        )
        adapter = MagicMock()
        adapter.preflight.return_value = None

        def build_command(
            _prompt_file: Path,
            _config: dict[str, object],
            variables: dict[str, str],
        ) -> list[str]:
            output_path = Path(variables["run.dir"]) / "analysis.txt"
            command = (
                f"sleep 0.05; mkdir -p {shlex.quote(str(output_path.parent))}; "
                f"printf done > {shlex.quote(str(output_path))}"
            )
            return ["/bin/sh", "-c", command]

        adapter.build_command.side_effect = build_command
        adapter.prepare_env.side_effect = lambda env, _config: env
        adapter.working_directory.return_value = None
        real_run_pool = RunPool

        def fast_run_pool(
            pool_config: RunPoolConfig | None = None,
            backend: LaunchBackend | None = None,
        ) -> RunPool:
            assert pool_config is not None
            return real_run_pool(
                pool_config.model_copy(update={"monitor_interval_s": 0.01}),
                backend=backend,
            )

        runs_dir = tmp_path / "runs"
        direct_launch = AsyncMock(side_effect=AssertionError("direct scalar launch used"))
        with (
            patch("metaproc.commands.run_process.get_adapter", return_value=adapter),
            patch("metaproc.commands.run_process.RunPool", side_effect=fast_run_pool),
            patch(
                "metaproc.commands.run_process._run_agent_subprocess",
                direct_launch,
            ),
        ):
            result = CliRunner().invoke(
                app,
                [
                    "run-process",
                    str(process_dir / "parent.process.md"),
                    "--var",
                    f"RUNS_DIR={runs_dir}",
                    "--var",
                    "RUN_ID=gtia-v30pre-real-pool-l0",
                    "--max-concurrency",
                    "2",
                ],
            )

        assert result.exit_code == 0, result.output
        assert direct_launch.await_count == 0
        run_dir = runs_dir / "gtia-v30pre-real-pool-l0"
        status = read_yaml_file(run_dir / STATE_DIR / "runpool-status.yaml")
        assert status["completed_count"] == 2
        assert status["failed_count"] == 0
        assert status["lanes"][0]["completed_count"] == 2
        pool_events = [
            json.loads(line)
            for line in (run_dir / LOGS_DIR / "runpool" / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert sum(event["event"] == "pool_start" for event in pool_events) == 1
        assert sum(event["event"] == "process_start" for event in pool_events) == 2
        assert sum(event["event"] == "process_exit" for event in pool_events) == 2


class TestCodeStepLogs:
    def test_command_stdout_lands_under_task_logs(self, tmp_path: Path) -> None:
        command = "/bin/sh -c 'printf hello; sleep 0.2'"
        step_def = ProcessStep(id="echo-output", mode="code", command=command)
        target = ResolvedStep(step_id="echo-output", mode="code", command=command)
        run_dir = tmp_path / "run"

        result = asyncio.run(
            _execute_code_step(
                spec=ProcessSpec(name="test"),
                step_def=step_def,
                target=target,
                variables={},
                process_dir=tmp_path,
                run_dir=run_dir,
                run_id="run-1",
                out=FakeOut(),
            )
        )

        assert result is True
        task_logs = sorted((run_dir / LOGS_DIR / "tasks" / "echo-output").glob("process_*.log"))
        assert len(task_logs) == 1
        assert task_logs[0].read_text(encoding="utf-8") == "hello"
        assert not list((run_dir / LOGS_DIR).glob("echo-output_*.log"))

        events = read_events(run_dir / LOGS_DIR / "resource-events.jsonl")
        samples = [event for event in events if isinstance(event, SampleEvent)]
        assert samples
        assert {event.hierarchy.step_node_id for event in samples} == {"echo-output"}
        assert all(event.metrics.rss_bytes_max is not None for event in samples)


# ── CLI integration via typer.testing ────────────────────────────


class TestCLIDryRun:
    """Test --dry-run via the CLI runner."""

    @pytest.mark.parametrize(
        ("extra_args", "env_value"),
        [
            (["--max-concurrency", "0"], None),
            ([], "0"),
        ],
    )
    def test_invalid_leaf_ceiling_fails_before_process_resolution(
        self,
        monkeypatch: pytest.MonkeyPatch,
        extra_args: list[str],
        env_value: str | None,
    ) -> None:
        monkeypatch.delenv("METAPROC_DEFAULT_MAX_CONCURRENCY", raising=False)
        if env_value is not None:
            monkeypatch.setenv("METAPROC_DEFAULT_MAX_CONCURRENCY", env_value)
        resolver = MagicMock()

        with patch(
            "metaproc.commands.run_process.resolve_process_path",
            resolver,
        ):
            result = CliRunner().invoke(
                app,
                ["run-process", "missing.process.md", "--dry-run", *extra_args],
            )

        assert result.exit_code != 0
        assert result.exception is not None
        assert "max_concurrency must be at least 1" in str(result.exception)
        resolver.assert_not_called()

    def test_dry_run_synthetic_process(self) -> None:
        """Dry-run on the checked-in synthetic process spec."""

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                _SYNTHETIC_PROCESS,
                "--var",
                "RUNS_DIR=/tmp/metaproc-test-runs",
                "--var",
                "RUN_ID=test-dry",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Level 0" in result.output
        assert "s1" in result.output
        assert "s3" in result.output

    def test_dry_run_with_skip(self) -> None:

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                _SYNTHETIC_PROCESS,
                "--var",
                "RUNS_DIR=/tmp/metaproc-test-runs",
                "--var",
                "RUN_ID=test-dry",
                "--dry-run",
                "--skip",
                "s2",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "[SKIP]" in result.output

    def test_invalid_skip_step(self) -> None:

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                _SYNTHETIC_PROCESS,
                "--var",
                "RUNS_DIR=/tmp/metaproc-test-runs",
                "--var",
                "RUN_ID=test-dry",
                "--skip",
                "nonexistent-step",
            ],
        )
        assert result.exit_code != 0
        assert result.exception is not None
        assert "not found" in str(result.exception)

    def test_continue_on_error_default_is_true(self) -> None:
        """Regression for P3.1.1 I1: the --continue-on-error flag defaults to True.

        Deadline-run reliability depends on per-step failures not aborting the whole
        DAG; the default must stay enabled so operators don't have to remember the
        flag on every live run. Per-item isolation within fan-out steps is separate
        and always on.
        """

        sig = inspect.signature(run_process_command)
        default = sig.parameters["continue_on_error"].default
        # typer.Option wraps the actual default; unwrap via its .default attribute.
        assert getattr(default, "default", default) is True

    def test_invalid_from_step(self) -> None:

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                _SYNTHETIC_PROCESS,
                "--var",
                "RUNS_DIR=/tmp/metaproc-test-runs",
                "--var",
                "RUN_ID=test-dry",
                "--from",
                "nonexistent-step",
            ],
        )
        assert result.exit_code != 0
        assert result.exception is not None
        assert "not found" in str(result.exception)


class TestCloudAuthDispatch:
    def test_cli_carries_complete_auth_cohort_to_orchestrator(self) -> None:
        dispatch_result = MagicMock(job_id="test-job", state="SUCCEEDED", exit_code=0)
        dispatch_orchestrator = AsyncMock(return_value=dispatch_result)

        with (
            patch(
                "metaproc.engine.preflight.run_cloud_preflight_warnings",
                return_value=[],
            ),
            patch("metaproc.engine.preflight.run_cloud_preflight", return_value=[]),
            patch(
                "metaproc.cloud.gcp.worker_dispatch.build_gcp_config_from_env",
                return_value=MagicMock(),
            ),
            patch(
                "metaproc.cloud.gcp.orchestrator_dispatch.dispatch_orchestrator",
                dispatch_orchestrator,
            ),
        ):
            result = CliRunner().invoke(
                app,
                [
                    "run-process",
                    _SYNTHETIC_PROCESS,
                    "--var",
                    "RUNS_DIR=/tmp/metaproc-test-runs",
                    "--var",
                    "RUN_ID=test-cloud-auth",
                    "--backend",
                    "gcp-worker",
                    "--cloud",
                    "--auth-account",
                    "claude-code-cli",
                    "--auth-backend",
                    "gcp-secret-manager",
                    "--auth-fallback-policy",
                    "same-provider",
                    "--auth-policy",
                    "least-active",
                    "--auth-include-labels",
                    "primary",
                    "--auth-include-labels",
                    "alternate",
                    "--no-auth-cross-quota-group",
                    "--auth-preflight-quota-guard",
                    "off",
                ],
            )

        assert result.exit_code == 0, result.output
        await_args = dispatch_orchestrator.await_args
        assert await_args is not None
        dispatch_config = await_args.args[0]
        assert dispatch_config.auth_flags == AuthPoolFlags(
            auth_account="claude-code-cli",
            auth_backend="gcp-secret-manager",
            auth_fallback_policy="same-provider",
            auth_policy="least-active",
            auth_include_labels=("primary", "alternate"),
            auth_cross_quota_group=False,
        )


class TestCLIStaleMetaprocWarning:
    """The stale-wheel warning must fire for GCP worker launch previews.

    Uses --dry-run so no Batch job is submitted; the warning is hoisted above
    dry-run so operators see it while iterating on an invocation.
    """

    def _invoke(self, *extra_args: str) -> tuple[int, str]:

        runner = CliRunner()
        # Pretend the tracked branch has metaproc/ edits and no wheel override.
        warning_tuple = (
            False,
            (
                "Metaproc artifact: tracked branch has 1 commit(s) ahead of "
                "origin/main under metaproc/ but METAPROC_WHEEL_GCS is not set — "
                "Batch will run the image-baked metaproc code."
            ),
        )
        with patch(
            "metaproc.engine.preflight.run_cloud_preflight_warnings",
            return_value=[warning_tuple],
        ):
            result = runner.invoke(
                app,
                [
                    "run-process",
                    _SYNTHETIC_PROCESS,
                    "--var",
                    "RUNS_DIR=/tmp/metaproc-test-runs",
                    "--var",
                    "RUN_ID=test-preflight-warn",
                    "--dry-run",
                    *extra_args,
                ],
            )
        # CliRunner merges stdout + stderr into result.output.
        return result.exit_code, result.output

    def test_gcp_worker_dry_run_emits_warning(self) -> None:
        exit_code, output = self._invoke("--backend", "gcp-worker")
        assert exit_code == 0, output
        assert "Cloud preflight warning" in output
        assert "METAPROC_WHEEL_GCS" in output

    def test_full_cloud_emits_warning(self) -> None:
        exit_code, output = self._invoke("--backend", "gcp-worker", "--cloud")
        assert exit_code == 0, output
        assert "Cloud preflight warning" in output
        assert "METAPROC_WHEEL_GCS" in output

    def test_local_backend_skips_warning(self) -> None:
        exit_code, output = self._invoke()  # default --backend local
        assert exit_code == 0, output
        assert "Cloud preflight warning" not in output


class TestManualStepExecution:
    def test_execute_manual_step_waits_for_ack(self, tmp_path: Path) -> None:

        out = MagicMock()
        step_def = ProcessStep(id="approve", mode="manual")
        target = ResolvedStep(step_id="approve", mode="manual")
        run_dir = tmp_path / "run"
        state_dir = _task_state_dir_for(run_dir, "approve")

        real_sleep = asyncio.sleep

        async def writer() -> None:
            await real_sleep(0)
            state_dir.mkdir(parents=True, exist_ok=True)
            write_manual_ack_at(
                state_dir,
                ManualAckRecord(
                    run_id="manual-test/test-run",
                    step_id="approve",
                    operator="alice",
                    acknowledged_at="2026-04-17T12:00:00",
                ),
            )

        async def fast_sleep(delay: float) -> None:
            await real_sleep(0)

        async def exercise() -> bool:
            writer_task = asyncio.create_task(writer())
            with patch("metaproc.commands.run_process.asyncio.sleep", side_effect=fast_sleep):
                result = await _execute_manual_step(
                    step_def=step_def,
                    target=target,
                    variables={"RUN_ID": "test-run"},
                    process_path=tmp_path / "test-manual.process.md",
                    run_dir=run_dir,
                    run_id="manual-test/test-run",
                    out=out,
                )
            await writer_task
            return result

        assert asyncio.run(exercise()) is True
        status = _read_step_status(run_dir, "approve")
        assert status is not None
        assert status.state == "completed"
        ack = read_manual_ack_at(state_dir)
        assert ack is not None
        assert ack.operator == "alice"

    def test_run_step_manual_writes_ack_and_status(self, tmp_path: Path) -> None:

        process_dir = tmp_path / "manual-process"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: manual-process
                  steps:
                    - id: approve
                      mode: manual
                ---
                """
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-step",
                str(process_dir / "test.process.md"),
                "--step",
                "approve",
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
                "--var",
                "RUN_ID=test-run",
                "--operator",
                "alice",
            ],
        )

        assert result.exit_code == 0, result.output
        run_dir = tmp_path / "runs" / "test-run"
        status = _read_step_status(run_dir, "approve")
        assert status is not None
        assert status.state == "completed"
        ack = read_manual_ack_at(_task_state_dir_for(run_dir, "approve"))
        assert ack is not None
        assert ack.operator == "alice"

    def test_run_process_uses_existing_manual_ack(self, tmp_path: Path) -> None:

        process_dir = tmp_path / "manual-process"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: manual-process
                  steps:
                    - id: approve
                      mode: manual
                ---
                """
            )
        )

        runner = CliRunner()
        ack_result = runner.invoke(
            app,
            [
                "run-step",
                str(process_dir / "test.process.md"),
                "--step",
                "approve",
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
                "--var",
                "RUN_ID=test-run",
                "--operator",
                "alice",
            ],
        )
        assert ack_result.exit_code == 0, ack_result.output

        result = runner.invoke(
            app,
            [
                "run-process",
                str(process_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
                "--var",
                "RUN_ID=test-run",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "already completed" in result.output


# ── gcp-worker backend integration ─────────────────────────────


class TestGCPWorkerBackendFlags:
    """Test that --backend gcp-worker flags are accepted and parsed correctly."""

    def test_gcp_worker_flags_in_dry_run(self, tmp_path: Path) -> None:
        """Verify gcp-worker-specific flags parse without error in dry-run mode.

        Uses a synthetic process spec to avoid path resolution issues.
        """

        # Create a minimal process spec in tmp_path
        process_dir = tmp_path / "test-process"
        process_dir.mkdir()
        spec_content = textwrap.dedent(
            """\
            ---
            process:
              name: test
              steps:
                - id: step-a
                  mode: code
                  handler: "echo:hello"
            ---
        """
        )
        (process_dir / "test.process.md").write_text(spec_content)

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                str(process_dir / "test.process.md"),
                "--dry-run",
                "--backend",
                "gcp-worker",
                "--num-workers",
                "3",
                "--machine-type",
                "n2-highmem-16",
                "--no-spot",
            ],
        )
        assert result.exit_code == 0, (
            f"exit={result.exit_code}, output={result.output}, exc={result.exception}"
        )
        assert "step-a" in result.output

    def test_default_flag_values(self, tmp_path: Path) -> None:
        """Verify default values for gcp-worker flags."""

        process_dir = tmp_path / "test-process"
        process_dir.mkdir()
        spec_content = textwrap.dedent(
            """\
            ---
            process:
              name: test
              steps:
                - id: step-a
                  mode: code
                  handler: "echo:hello"
            ---
        """
        )
        (process_dir / "test.process.md").write_text(spec_content)

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                str(process_dir / "test.process.md"),
                "--dry-run",
                "--backend",
                "gcp-worker",
            ],
        )
        assert result.exit_code == 0, (
            f"exit={result.exit_code}, output={result.output}, exc={result.exception}"
        )

    def test_laptop_orchestrator_is_rejected(self) -> None:
        for task_index in (None, "", "  "):
            with pytest.raises(CLIError, match="without --cloud"):
                validate_gcp_worker_topology(
                    "gcp-worker",
                    cloud=False,
                    batch_task_index=task_index,
                )

    def test_batch_orchestrator_inner_leg_is_allowed(self) -> None:
        validate_gcp_worker_topology(
            "gcp-worker",
            cloud=False,
            batch_task_index="0",
        )

    def test_cli_rejects_laptop_orchestrator_before_creating_run_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runs_dir = tmp_path / "runs"
        monkeypatch.delenv("BATCH_TASK_INDEX", raising=False)
        with (
            patch("metaproc.commands.run_process._preflight_plan_adapters", return_value=[]),
            patch("metaproc.engine.preflight.run_cloud_preflight_warnings", return_value=[]),
        ):
            result = CliRunner().invoke(
                app,
                [
                    "run-process",
                    _SYNTHETIC_PROCESS,
                    "--var",
                    f"RUNS_DIR={runs_dir}",
                    "--var",
                    "RUN_ID=unsupported-laptop-orchestrator",
                    "--backend",
                    "gcp-worker",
                ],
            )

        assert result.exit_code != 0
        assert isinstance(result.exception, CLIError)
        assert "without --cloud" in str(result.exception)
        assert not runs_dir.exists()


class TestProcessContractValidation:
    def test_deferred_fan_out_keeps_already_accepted_resume_item(self, tmp_path: Path) -> None:
        step = ProcessStep(
            id="predict",
            mode="agent",
            for_each=ForEach(
                over="deps.tickers",
                bind="ticker",
                bind_fields=["ticker"],
                key="{{ticker}}",
            ),
        )
        item_context = {"ticker": "AAPL"}
        state_dir = compute_task_state_dir(tmp_path, step, item_context)
        running = mark_running_at(
            state_dir,
            run_id="process/run",
            step_id="predict",
            item=item_context,
            item_key="AAPL",
        )
        mark_completed_at(state_dir, running_record=running)

        results = _finish_deferred_fan_out_attempts(
            results=[("AAPL", 0)],
            item_contexts=[item_context],
            each="ticker",
            variables={},
            step_def=step,
            step_id="predict",
            run_dir=tmp_path,
            run_id="process/run",
            outputs={},
            boundary_error="write boundary violated: current-attempt-stray.md",
            step_hash=None,
        )

        assert results == [("AAPL", 0)]
        status = read_status_at(state_dir)
        assert status is not None
        assert status.state == "completed"
        history = read_attempt_history_at(state_dir)
        assert [record.disposition for record in history] == [AttemptDisposition.succeeded]

    @pytest.mark.parametrize(
        ("boundary_error", "expected_state", "expected_disposition", "expected_code"),
        [
            (None, "completed", AttemptDisposition.succeeded, 0),
            (
                "write boundary violated: stray.md",
                "failed",
                AttemptDisposition.permanent,
                1,
            ),
        ],
    )
    def test_deferred_fan_out_attempt_finishes_after_boundary_validation(
        self,
        tmp_path: Path,
        boundary_error: str | None,
        expected_state: str,
        expected_disposition: AttemptDisposition,
        expected_code: int,
    ) -> None:
        step = ProcessStep(
            id="predict",
            mode="agent",
            for_each=ForEach(
                over="deps.tickers",
                bind="ticker",
                bind_fields=["ticker"],
                key="{{ticker}}",
            ),
        )
        item_context = {"ticker": "AAPL"}
        state_dir = compute_task_state_dir(tmp_path, step, item_context)
        mark_running_at(
            state_dir,
            run_id="process/run",
            step_id="predict",
            item=item_context,
            item_key="AAPL",
        )

        results = _finish_deferred_fan_out_attempts(
            results=[("AAPL", 0)],
            item_contexts=[item_context],
            each="ticker",
            variables={},
            step_def=step,
            step_id="predict",
            run_dir=tmp_path,
            run_id="process/run",
            outputs={},
            boundary_error=boundary_error,
            step_hash=None,
        )

        assert results == [("AAPL", expected_code)]
        status = read_status_at(state_dir)
        assert status is not None
        assert status.state == expected_state
        history = read_attempt_history_at(state_dir)
        assert [record.disposition for record in history] == [expected_disposition]

    def test_process_output_ref_reexport_resolves_step_output(self, tmp_path: Path) -> None:

        process_dir = tmp_path / "output-ref-contract"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: output-ref-contract
                  outputs:
                    final_report:
                      ref: build.report
                      as: path
                  steps:
                    - id: build
                      mode: code
                      command: >-
                        mkdir -p {{run.dir}}/final &&
                        touch {{run.dir}}/final/out.md
                      output_root: "{{run.dir}}/final"
                      outputs:
                        report:
                          path: out.md
                ---
                """
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                str(process_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
                "--var",
                "RUN_ID=test-run",
            ],
        )

        assert result.exit_code == 0, result.output
        assert (tmp_path / "runs" / "test-run" / "final" / "out.md").exists()

    def test_missing_process_output_fails_after_execution(self, tmp_path: Path) -> None:

        process_dir = tmp_path / "output-contract"
        process_dir.mkdir()
        (process_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: output-contract
                  outputs:
                    final_report:
                      path: "{{run.dir}}/final/out.md"
                      as: path
                  steps:
                    - id: noop
                      mode: code
                      command: "true"
                ---
                """
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                str(process_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
                "--var",
                "RUN_ID=test-run",
            ],
        )

        assert result.exit_code != 0
        error_text = result.output
        if result.exception is not None:
            error_text += f"\n{result.exception}"
        assert "process output validation failed" in error_text
        assert "final_report" in error_text
        assert "out.md" in error_text

    def test_composite_child_inputs_are_validated(self, tmp_path: Path) -> None:

        parent_dir = tmp_path / "parent"
        child_dir = parent_dir / "child"
        child_dir.mkdir(parents=True)

        (parent_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: parent
                  deps:
                    child_process:
                      path: "./child/test.process.md"
                      as: path
                      role: process
                  steps:
                    - id: child
                      mode: composite
                      uses: deps.child_process
                ---
                """
            )
        )
        (child_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: child
                  inputs:
                    ticker:
                      param: TICKER
                      as: string
                  steps:
                    - id: noop
                      mode: manual
                ---
                """
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                str(parent_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
                "--var",
                "RUN_ID=test-run",
            ],
        )

        assert result.exit_code != 0
        error_text = result.output
        if result.exception is not None:
            error_text += f"\n{result.exception}"
        assert "child process input validation failed" in error_text
        assert "TICKER" in error_text

    def test_run_process_fails_agent_write_outside_declared_boundary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        repo_dir = tmp_path / "boundary-repo"
        process_dir = repo_dir / "boundary-process"
        process_dir.mkdir(parents=True)
        subprocess.run(
            ["git", "init"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )

        allowed_path = repo_dir / "runs" / "test-run" / "boundary" / "out.md"
        rogue_path = repo_dir / "runs" / "test-run" / "boundary" / "stray.md"

        (process_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: boundary
                  defaults:
                    default_adapter: boundary-test
                    adapters:
                      boundary-test:
                        type: boundary-test
                  inputs:
                    allowed_path: { param: ALLOWED_PATH, as: path }
                    rogue_path: { param: ROGUE_PATH, as: path }
                  steps:
                    - id: agent-step
                      mode: agent
                      prompt_prefix: write both files
                      outputs:
                        main:
                          path: "{{ALLOWED_PATH}}"
                          kind: file
                ---
                """
            )
        )

        class BoundaryAdapter:
            adapter_type = "boundary-test"
            short_name = "boundary-test"
            default_model = None

            def build_command(self, prompt_file, merged_config, variables):
                script = (
                    "from pathlib import Path; "
                    f"allowed = Path({variables['ALLOWED_PATH']!r}); "
                    "allowed.parent.mkdir(parents=True, exist_ok=True); "
                    "allowed.write_text('ok'); "
                    f"rogue = Path({variables['ROGUE_PATH']!r}); "
                    "rogue.parent.mkdir(parents=True, exist_ok=True); "
                    "rogue.write_text('bad')"
                )
                return [sys.executable, "-c", script]

            def prepare_env(self, env, merged_config):
                return env

            def working_directory(self, merged_config):
                return None

            def parse_result_event(self, line):
                return None

            def check_auth(self):
                raise NotImplementedError

            def auth_info(self):
                return ""

        monkeypatch.setitem(ADAPTER_REGISTRY, "boundary-test", BoundaryAdapter())

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                str(process_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={repo_dir / 'runs'}",
                "--var",
                "RUN_ID=test-run",
                "--var",
                f"ALLOWED_PATH={allowed_path}",
                "--var",
                f"ROGUE_PATH={rogue_path}",
            ],
        )

        assert result.exit_code != 0
        assert rogue_path.exists()
        # Per-task state lives at <run>/.state/tasks/<step_id>/ under the
        # new run-dir layout (plan-2026-05-10-metaproc-run-dir-layout.md).
        step_state_dir = _task_state_dir_for(repo_dir / "runs" / "test-run", "agent-step")
        status = read_status_at(step_state_dir)
        assert status is not None
        assert status.state == "failed"
        assert status.error is not None
        assert "write boundary violated" in status.error
        assert "stray.md" in status.error

    def test_boundary_check_ignores_external_changes_outside_observation_zone(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Concurrent writes outside run_dir must not fail the step.

        Regression test for the fix: boundary snapshot formerly covered
        the entire repo, so any external write during the step (e.g. another
        process editing source files) was falsely attributed to the agent.
        The observation zone must be scoped to run_dir.
        """

        repo_dir = tmp_path / "boundary-repo"
        process_dir = repo_dir / "boundary-process"
        process_dir.mkdir(parents=True)
        subprocess.run(
            ["git", "init"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )

        allowed_path = repo_dir / "runs" / "test-run" / "boundary" / "out.md"
        external_path = repo_dir / "external-concurrent.txt"

        (process_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: boundary
                  defaults:
                    default_adapter: boundary-test
                    adapters:
                      boundary-test:
                        type: boundary-test
                  inputs:
                    allowed_path: { param: ALLOWED_PATH, as: path }
                    external_path: { param: EXTERNAL_PATH, as: path }
                  steps:
                    - id: agent-step
                      mode: agent
                      prompt_prefix: write allowed and simulate concurrent external write
                      outputs:
                        main:
                          path: "{{ALLOWED_PATH}}"
                          kind: file
                ---
                """
            )
        )

        class BoundaryAdapter:
            adapter_type = "boundary-test"
            short_name = "boundary-test"
            default_model = None

            def build_command(self, prompt_file, merged_config, variables):
                script = (
                    "from pathlib import Path; "
                    f"allowed = Path({variables['ALLOWED_PATH']!r}); "
                    "allowed.parent.mkdir(parents=True, exist_ok=True); "
                    "allowed.write_text('ok'); "
                    f"external = Path({variables['EXTERNAL_PATH']!r}); "
                    "external.parent.mkdir(parents=True, exist_ok=True); "
                    "external.write_text('concurrent external write')"
                )
                return [sys.executable, "-c", script]

            def prepare_env(self, env, merged_config):
                return env

            def working_directory(self, merged_config):
                return None

            def parse_result_event(self, line):
                return None

            def check_auth(self):
                raise NotImplementedError

            def auth_info(self):
                return ""

        monkeypatch.setitem(ADAPTER_REGISTRY, "boundary-test", BoundaryAdapter())

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                str(process_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={repo_dir / 'runs'}",
                "--var",
                "RUN_ID=test-run",
                "--var",
                f"ALLOWED_PATH={allowed_path}",
                "--var",
                f"EXTERNAL_PATH={external_path}",
            ],
        )

        assert result.exit_code == 0, (
            f"step should succeed when external changes are outside observation zone; "
            f"stdout={result.stdout!r}"
        )
        assert external_path.exists()
        assert allowed_path.exists()
        # Per-task state lives at <run>/.state/tasks/<step_id>/ under the
        # new run-dir layout (plan-2026-05-10-metaproc-run-dir-layout.md).
        step_state_dir = _task_state_dir_for(repo_dir / "runs" / "test-run", "agent-step")
        status = read_status_at(step_state_dir)
        assert status is not None
        assert status.state == "completed"

    def test_agent_adapter_receives_existing_prompt_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Adapters that inline prompts must receive a real file before dispatch."""

        repo_dir = tmp_path / "prompt-repo"
        process_dir = repo_dir / "prompt-process"
        process_dir.mkdir(parents=True)
        subprocess.run(
            ["git", "init"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )

        output_path = repo_dir / "runs" / "test-run" / "prompt" / "out.md"
        (process_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: prompt-order
                  defaults:
                    default_adapter: prompt-test
                    adapters:
                      prompt-test:
                        type: prompt-test
                  inputs:
                    output_path: { param: OUTPUT_PATH, as: path }
                  steps:
                    - id: agent-step
                      mode: agent
                      prompt_prefix: write the declared output
                      outputs:
                        main:
                          path: "{{OUTPUT_PATH}}"
                          kind: file
                ---
                """
            )
        )

        class PromptInliningAdapter:
            adapter_type = "prompt-test"
            short_name = "prompt-test"
            default_model = None

            def build_command(self, prompt_file, merged_config, variables):
                path = Path(prompt_file)
                assert path.exists()
                assert "write the declared output" in path.read_text()
                script = (
                    "from pathlib import Path; "
                    f"target = Path({variables['OUTPUT_PATH']!r}); "
                    "target.parent.mkdir(parents=True, exist_ok=True); "
                    "target.write_text('ok')"
                )
                return [sys.executable, "-c", script]

            def prepare_env(self, env, merged_config):
                return env

            def working_directory(self, merged_config):
                return None

            def parse_result_event(self, line):
                return None

            def check_auth(self):
                raise NotImplementedError

            def auth_info(self):
                return ""

        monkeypatch.setitem(ADAPTER_REGISTRY, "prompt-test", PromptInliningAdapter())

        result = CliRunner().invoke(
            app,
            [
                "run-process",
                str(process_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={repo_dir / 'runs'}",
                "--var",
                "RUN_ID=test-run",
                "--var",
                f"OUTPUT_PATH={output_path}",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert output_path.read_text() == "ok"

    def test_boundary_check_accepts_relative_runs_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Legitimate writes must not be flagged when RUNS_DIR is relative.

        Regression test for the fix. Previously, allowed_targets came
        out as relative paths (from ``Path(resolve_templates(...))`` with a
        relative ``RUNS_DIR``) while changed_paths were absolute (resolved
        against repo_root). ``is_path_under`` rejected the mismatch and
        flagged every legitimate write as a boundary violation. The bug
        hid behind the ``failed_count == 0`` gate and behind the test
        suite's use of absolute ``tmp_path`` paths.
        """

        repo_dir = tmp_path / "boundary-repo"
        process_dir = repo_dir / "boundary-process"
        process_dir.mkdir(parents=True)
        subprocess.run(
            ["git", "init"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )

        allowed_rel = Path("runs") / "test-run" / "boundary" / "out.md"

        (process_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: boundary
                  defaults:
                    default_adapter: boundary-test
                    adapters:
                      boundary-test:
                        type: boundary-test
                  inputs:
                    allowed_path: { param: ALLOWED_PATH, as: path }
                  steps:
                    - id: agent-step
                      mode: agent
                      prompt_prefix: write allowed file
                      outputs:
                        main:
                          path: "{{ALLOWED_PATH}}"
                          kind: file
                ---
                """
            )
        )

        class BoundaryAdapter:
            adapter_type = "boundary-test"
            short_name = "boundary-test"
            default_model = None

            def build_command(self, prompt_file, merged_config, variables):
                script = (
                    "from pathlib import Path; "
                    f"allowed = Path({variables['ALLOWED_PATH']!r}); "
                    "allowed.parent.mkdir(parents=True, exist_ok=True); "
                    "allowed.write_text('ok')"
                )
                return [sys.executable, "-c", script]

            def prepare_env(self, env, merged_config):
                return env

            def working_directory(self, merged_config):
                return None

            def parse_result_event(self, line):
                return None

            def check_auth(self):
                raise NotImplementedError

            def auth_info(self):
                return ""

        monkeypatch.setitem(ADAPTER_REGISTRY, "boundary-test", BoundaryAdapter())

        monkeypatch.chdir(repo_dir)

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                "boundary-process/test.process.md",
                "--var",
                "RUNS_DIR=runs",
                "--var",
                "RUN_ID=test-run",
                "--var",
                f"ALLOWED_PATH={allowed_rel}",
            ],
        )

        assert result.exit_code == 0, (
            f"step should succeed when RUNS_DIR is relative and all writes are "
            f"inside the declared boundary; stdout={result.stdout!r}"
        )
        allowed_abs = repo_dir / allowed_rel
        assert allowed_abs.exists()
        # Per-task state lives at <run>/.state/tasks/<step_id>/ under the
        # new run-dir layout (plan-2026-05-10-metaproc-run-dir-layout.md).
        step_state_dir = _task_state_dir_for(repo_dir / "runs" / "test-run", "agent-step")
        status = read_status_at(step_state_dir)
        assert status is not None
        assert status.state == "completed"

    def test_two_nonforeach_agent_steps_sharing_output_parent_keep_state_separate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two non-for_each agent steps that output sibling files in the same dir
        must each keep their own .state/status.yaml.

        Before the fix, both steps used compute_item_dir which returned the
        output's parent — `<run_dir>/mine/<variant>/`. Both wrote
        `.state/status.yaml` and `.state/attempt.yaml` to that shared dir, so
        whichever step ran second clobbered the first's status. If the first
        step had failed, its error context was lost.

        Post-fix: each step writes to its canonical step-scoped path,
        `<run_dir>/<step_id>/.state/`. State for both steps is recoverable.
        """

        repo_dir = tmp_path / "collision-repo"
        process_dir = repo_dir / "collision-process"
        process_dir.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)

        # Two outputs share the same parent dir — `<run_dir>/shared/`.
        summary_path = repo_dir / "runs" / "test-run" / "shared" / "summary.md"
        review_path = repo_dir / "runs" / "test-run" / "shared" / "review.md"

        (process_dir / "test.process.md").write_text(
            textwrap.dedent(
                """\
                ---
                process:
                  name: collision
                  defaults:
                    default_adapter: collision-test
                    adapters:
                      collision-test:
                        type: collision-test
                  inputs:
                    summary_path: { param: SUMMARY_PATH, as: path }
                    review_path: { param: REVIEW_PATH, as: path }
                  steps:
                    - id: write-summary
                      mode: agent
                      prompt_prefix: write summary
                      outputs:
                        main:
                          path: "{{SUMMARY_PATH}}"
                          kind: file
                    - id: write-review
                      mode: agent
                      prompt_prefix: write review
                      needs: [write-summary]
                      outputs:
                        main:
                          path: "{{REVIEW_PATH}}"
                          kind: file
                ---
                """
            )
        )

        class CollisionAdapter:
            adapter_type = "collision-test"
            short_name = "collision-test"
            default_model = None

            def build_command(self, prompt_file, merged_config, variables):
                prompt_text = Path(prompt_file).read_text()
                if "summary" in prompt_text:
                    target_path = variables["SUMMARY_PATH"]
                else:
                    target_path = variables["REVIEW_PATH"]
                script = (
                    "from pathlib import Path; "
                    f"target = Path({target_path!r}); "
                    "target.parent.mkdir(parents=True, exist_ok=True); "
                    "target.write_text('---\\nstatus: ok\\n---\\n')"
                )
                return [sys.executable, "-c", script]

            def prepare_env(self, env, merged_config):
                return env

            def working_directory(self, merged_config):
                return None

            def parse_result_event(self, line):
                return None

            def check_auth(self):
                raise NotImplementedError

            def auth_info(self):
                return ""

        monkeypatch.setitem(ADAPTER_REGISTRY, "collision-test", CollisionAdapter())

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-process",
                str(process_dir / "test.process.md"),
                "--var",
                f"RUNS_DIR={repo_dir / 'runs'}",
                "--var",
                "RUN_ID=test-run",
                "--var",
                f"SUMMARY_PATH={summary_path}",
                "--var",
                f"REVIEW_PATH={review_path}",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert summary_path.exists()
        assert review_path.exists()

        # Both step states are recoverable, NOT clobbered. Each non-fan-out
        # step writes status to its own <run>/.state/tasks/<step_id>/status.yaml,
        # so two steps that share an output parent dir keep separate state.
        run_dir = repo_dir / "runs" / "test-run"
        summary_status = read_status_at(_task_state_dir_for(run_dir, "write-summary"))
        review_status = read_status_at(_task_state_dir_for(run_dir, "write-review"))
        assert summary_status is not None
        assert summary_status.state == "completed"
        assert summary_status.step_id == "write-summary"
        assert review_status is not None
        assert review_status.state == "completed"
        assert review_status.step_id == "write-review"

        # Run-level .state/ holds only run-level state (process-status.yaml,
        # run-config.yaml) plus the steps/ and tasks/ sub-namespaces — never
        # an unscoped status.yaml or attempt.yaml.
        run_state_files = sorted((run_dir / ".state").iterdir())
        run_state_names = [p.name for p in run_state_files]
        assert "status.yaml" not in run_state_names
        assert "attempt.yaml" not in run_state_names


class TestGCPWorkerResumeAdoption:
    def test_reconciled_workers_are_not_redispatched(self, tmp_path: Path) -> None:
        out = MagicMock()
        gcp_config = MagicMock()
        gcp_config.filestore_server = "10.0.0.1"
        gcp_config.filestore_mount_path = "/mnt/filestore"

        async def fake_reconcile(**kwargs):
            return [MagicMock(exit_code=0)]

        async def fail_dispatch(**kwargs):
            raise AssertionError("dispatch_to_workers should not be called")

        with (
            patch(
                "metaproc.commands.run_process.build_gcp_config_from_env",
                return_value=gcp_config,
            ),
            patch(
                "metaproc.commands.run_process.reconcile_dispatched_workers",
                side_effect=fake_reconcile,
            ),
            patch(
                "metaproc.commands.run_process.dispatch_to_workers",
                side_effect=fail_dispatch,
            ),
        ):
            success = asyncio.run(
                _execute_gcp_worker_dispatch(
                    item_contexts=[{"EVENT_ID": "AAPL"}],
                    each="EVENT_ID",
                    step_id="generate-record",
                    process_path=tmp_path / "test.process.md",
                    variables={"RUN_ID": "run-1"},
                    run_dir=tmp_path,
                    num_workers=2,
                    max_concurrency=5,
                    machine_type="n2-standard-4",
                    spot=True,
                    variant="pi-test",
                    adapter_config={},
                    max_retries=None,
                    out=out,
                )
            )

        assert success is True

    def test_initial_dispatch_runs_when_no_manifest_exists(self, tmp_path: Path) -> None:
        out = MagicMock()
        gcp_config = MagicMock()
        gcp_config.filestore_server = "10.0.0.1"
        gcp_config.filestore_mount_path = "/mnt/filestore"
        captured: dict[str, object] = {}

        async def fake_reconcile(**kwargs):
            return None

        async def fake_dispatch(**kwargs):
            captured["item_contexts"] = kwargs["item_contexts"]
            return [MagicMock(exit_code=0)]

        with (
            patch(
                "metaproc.commands.run_process.build_gcp_config_from_env",
                return_value=gcp_config,
            ),
            patch(
                "metaproc.commands.run_process.reconcile_dispatched_workers",
                side_effect=fake_reconcile,
            ),
            patch(
                "metaproc.commands.run_process.dispatch_to_workers",
                side_effect=fake_dispatch,
            ),
        ):
            success = asyncio.run(
                _execute_gcp_worker_dispatch(
                    item_contexts=[{"EVENT_ID": "AAPL"}, {"EVENT_ID": "MSFT"}],
                    each="EVENT_ID",
                    step_id="generate-record",
                    process_path=tmp_path / "test.process.md",
                    variables={"RUN_ID": "run-1"},
                    run_dir=tmp_path,
                    num_workers=2,
                    max_concurrency=5,
                    machine_type="n2-standard-4",
                    spot=True,
                    variant="pi-test",
                    adapter_config={},
                    max_retries=None,
                    out=out,
                )
            )

        assert success is True
        assert captured["item_contexts"] == [{"EVENT_ID": "AAPL"}, {"EVENT_ID": "MSFT"}]


class TestCompositePoolDispatchPropagation:
    """Regression tests for recursive auth-pool policy propagation.

    The parent and child must use the same execution context. Otherwise, a
    composite-child fan-out can lose its pool dispatch policy and fall back to ambient
    credentials instead of the pool's load-balancing policy.

    The failure mode discovered 2026-05-21: tier1 ran 3 hours of Claude work but
    `metaproc auth usage <tier1>` reported 0 invocations on alt1/alt2.
    """

    def test_execute_composite_step_accepts_one_execution_context(self) -> None:
        """Recursive auth policy travels through the shared run context."""

        sig = inspect.signature(_execute_composite_step)
        params = sig.parameters
        assert "execution_context" in params
        assert "pool_dispatch_template" not in params
        assert "auth_flags" not in params
        assert "preflight_quota_guard" not in params

    def test_execute_composite_step_passes_pool_dispatch_to_child_orchestrate(self, tmp_path: Path):
        """The child orchestrator receives the parent's pool dispatch policy."""

        # Build a minimal composite step definition + target
        # The function early-returns if child_spec_path doesn't exist.
        child_spec_path = tmp_path / "child.process.md"
        child_spec_path.write_text("---\nprocess:\n  name: child\n  steps: []\n---\n# Child\n")

        step_def = MagicMock()
        step_def.id = "analysis-research"
        step_def.with_ = None
        step_def.for_each = None

        target = MagicMock()
        target.uses_path = str(child_spec_path)
        target.artifact_namespace = "test-ns"

        # Use a sentinel — we only care that THIS object survives the trip;
        # don't need a real coordinator etc. to validate propagation.
        sentinel_pool = MagicMock(spec=PoolDispatchConfig)

        captured: dict[str, object] = {}

        async def fake_orchestrate(**kwargs: object) -> None:
            captured["execution_context"] = kwargs["execution_context"]

        with (
            _test_execution_context(
                max_concurrency=4,
                pool_dispatch_template=sentinel_pool,
            ) as execution_context,
            patch(
                "metaproc.commands.run_process.load_process_spec",
                return_value=MagicMock(steps=[]),
            ),
            patch(
                "metaproc.commands.run_process.expand_process_vars",
                side_effect=lambda spec, vars, process_dir: vars,
            ),
            patch(
                "metaproc.commands.run_process.validate_spec_placeholders",
                return_value=[],
            ),
            patch(
                "metaproc.commands.run_process.validate_process_inputs",
                return_value=[],
            ),
            patch(
                "metaproc.commands.run_process.build_plan",
                return_value=MagicMock(steps=[]),
            ),
            patch(
                "metaproc.commands.run_process._orchestrate",
                side_effect=fake_orchestrate,
            ),
        ):
            out = MagicMock()
            asyncio.run(
                _execute_composite_step(
                    step_def=step_def,
                    target=target,
                    variables={"RUN_ID": "test-run"},
                    process_dir=tmp_path,
                    run_dir=tmp_path,
                    run_id="test-run",
                    scope_path=(),
                    execution_context=execution_context,
                    out=out,
                )
            )

        child_context = cast(RunExecutionContext, captured["execution_context"])
        assert child_context.pool_dispatch_template is sentinel_pool, (
            "pool_dispatch_template was DROPPED at the composite-child boundary — "
            "this is this regression: child run_parallel calls "
            "will see pool_dispatch=None and bypass the auth pool."
        )


class TestNonFanOutContentRetry:
    """A non-fan-out agent step recovers from a content failure the way a fan-out item does.

    ``on_invalid`` is declared on the output, so it has to be honored wherever the
    output is produced. Reading it on only one execution path makes the same
    declaration mean different things depending on whether the step happens to fan
    out, which is what these tests pin down.
    """

    @staticmethod
    def _write_process(
        process_dir: Path,
        on_invalid: str,
        *,
        max_retries: int = 3,
        output_extra: str = "",
    ) -> Path:
        """Write the spec. ``on_invalid`` and ``output_extra`` are YAML nested under
        the output, or empty."""
        body = textwrap.dedent("""\
            ---
            process:
              name: flaky
              defaults:
                default_adapter: flaky-test
                adapters:
                  flaky-test:
                    type: flaky-test
                retry:
                  max_retries: 3
                  initial_backoff_s: 0
              inputs:
                target_path: { param: TARGET_PATH, as: path }
              steps:
                - id: write-thing
                  mode: agent
                  prompt_prefix: write the thing
                  outputs:
                    main:
                      path: "{{TARGET_PATH}}"
                      kind: file
            ---
            """)
        assert "max_retries: 3" in body
        body = body.replace("max_retries: 3", f"max_retries: {max_retries}")
        extra = "\n".join(part for part in (output_extra, on_invalid) if part)
        if extra:
            # Sit beside `path:`/`kind:` under the output, at the dedented indent.
            anchor = " " * 10 + "kind: file\n"
            assert anchor in body
            clause = textwrap.indent(textwrap.dedent(extra).strip("\n"), " " * 10)
            body = body.replace(anchor, f"{anchor}{clause}\n")
        spec = process_dir / "test.process.md"
        spec.write_text(body)
        return spec

    @staticmethod
    def _register_adapter(
        monkeypatch: pytest.MonkeyPatch,
        counter: Path,
        *,
        succeed_after: int = 1,
        invalid_content: str | None = None,
        require_prompt_fragment: str | None = None,
        observed_prompts: list[str] | None = None,
    ) -> None:
        """An adapter whose first ``succeed_after`` calls write ``invalid_content``
        (or nothing when ``None``), and whose later calls write a valid artifact."""

        class FlakyAdapter:
            adapter_type = "flaky-test"
            short_name = "flaky-test"
            default_model = None

            def build_command(self, prompt_file, merged_config, variables):  # noqa: ANN001, ARG002
                prompt = Path(prompt_file).read_text()
                if observed_prompts is not None:
                    observed_prompts.append(prompt)
                prompt_allows_success = (
                    require_prompt_fragment is None or require_prompt_fragment in prompt
                )
                target_path = variables["TARGET_PATH"]
                bad_write = (
                    f"target.write_text({invalid_content!r})"
                    if invalid_content is not None
                    else "None"
                )
                script = (
                    "from pathlib import Path; "
                    f"counter = Path({str(counter)!r}); "
                    "n = len(counter.read_text()) if counter.exists() else 0; "
                    "counter.write_text('x' * (n + 1)); "
                    f"target = Path({target_path!r}); "
                    "target.parent.mkdir(parents=True, exist_ok=True); "
                    "target.write_text('---\\nstatus: ok\\n---\\n') "
                    f"if n >= {succeed_after} and {prompt_allows_success!r} else {bad_write}"
                )
                return [sys.executable, "-c", script]

            def prepare_env(self, env, merged_config):  # noqa: ANN001, ARG002
                return env

            def working_directory(self, merged_config):  # noqa: ANN001, ARG002
                return None

            def parse_result_event(self, line):  # noqa: ANN001, ARG002
                return None

            def check_auth(self):
                raise NotImplementedError

            def auth_info(self):
                return ""

        monkeypatch.setitem(ADAPTER_REGISTRY, "flaky-test", FlakyAdapter())

    def _run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        on_invalid: str,
        run_id: str,
        *,
        max_retries: int = 3,
        output_extra: str = "",
        succeed_after: int = 1,
        invalid_content: str | None = None,
        require_prompt_fragment: str | None = None,
    ):
        """Run the spec once. ``run_id`` also keys the scalar-admission pool, so two
        tests sharing one would contend for the same host slots."""
        repo_dir = tmp_path / "flaky-repo"
        process_dir = repo_dir / "flaky-process"
        process_dir.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)

        target = repo_dir / "runs" / run_id / "thing.md"
        counter = repo_dir / "counter.txt"
        spec = self._write_process(
            process_dir, on_invalid, max_retries=max_retries, output_extra=output_extra
        )
        observed_prompts: list[str] = []
        self._register_adapter(
            monkeypatch,
            counter,
            succeed_after=succeed_after,
            invalid_content=invalid_content,
            require_prompt_fragment=require_prompt_fragment,
            observed_prompts=observed_prompts,
        )

        result = CliRunner().invoke(
            app,
            [
                "run-process",
                str(spec),
                "--var",
                f"RUNS_DIR={repo_dir / 'runs'}",
                "--var",
                f"RUN_ID={run_id}",
                "--var",
                f"TARGET_PATH={target}",
            ],
        )
        status = read_status_at(_task_state_dir_for(repo_dir / "runs" / run_id, "write-thing"))
        calls = len(counter.read_text()) if counter.exists() else 0
        return result, status, calls, target, observed_prompts

    def test_missing_output_is_retried_and_the_second_attempt_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, status, calls, target, _prompts = self._run(
            tmp_path, monkeypatch, on_invalid="", run_id="retry-run"
        )

        assert result.exit_code == 0, result.stdout
        assert calls == 2, f"the adapter ran {calls}x; a missing output should be retried once"
        assert target.exists()
        assert status is not None
        assert status.state == "completed"
        assert status.attempt == 2, "the recovering attempt should be recorded, not hidden"
        history = read_attempt_history_at(_task_state_dir_for(target.parent, "write-thing"))
        assert [record.disposition for record in history] == [
            AttemptDisposition.retryable,
            AttemptDisposition.succeeded,
        ]

    def test_on_invalid_fail_makes_the_same_failure_terminal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, status, calls, _, _prompts = self._run(
            tmp_path,
            monkeypatch,
            on_invalid="on_invalid:\n  missing: fail",
            run_id="no-retry-run",
        )

        assert result.exit_code != 0
        assert calls == 1, f"the adapter ran {calls}x; `missing: fail` must not retry"
        assert status is not None
        assert status.state == "failed"
        assert status.attempt == 1

    def test_retries_stop_at_the_content_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, status, calls, target, _prompts = self._run(
            tmp_path,
            monkeypatch,
            on_invalid="",
            run_id="exhaust-run",
            max_retries=1,
            succeed_after=99,
        )

        assert result.exit_code != 0
        assert calls == 2, f"the adapter ran {calls}x; max_retries=1 allows exactly one retry"
        assert not target.exists()
        assert status is not None
        assert status.state == "failed"
        assert status.attempt == 2, "the exhausted attempt should be the one recorded"
        history = read_attempt_history_at(_task_state_dir_for(target.parent, "write-thing"))
        assert [record.disposition for record in history] == [
            AttemptDisposition.retryable,
            AttemptDisposition.retryable,
        ]

    def test_repair_saves_the_attempt_instead_of_burning_a_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A repairable frontmatter defect is fixed in place, with no second call.

        The adapter here never produces a clean artifact on its own, so this passes
        only if the repair pass actually ran before validation on the scalar path.
        """
        broken = "---\nrecord:\n  detail: Strong beat (Note: actually Q1 not Q2)\n---\nbody\n"
        result, status, calls, target, _prompts = self._run(
            tmp_path,
            monkeypatch,
            on_invalid="",
            run_id="repair-run",
            output_extra="format: frontmatter-md",
            succeed_after=99,
            invalid_content=broken,
        )

        assert result.exit_code == 0, result.stdout
        assert calls == 1, f"the adapter ran {calls}x; repair should save the first attempt"
        assert '"Strong beat (Note: actually Q1 not Q2)"' in target.read_text()
        assert status is not None
        assert status.state == "completed"
        assert status.attempt == 1

    def test_invalid_output_retry_tells_the_second_attempt_what_to_correct(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        feedback_header = "The prior attempt's declared output failed validation."
        result, status, calls, target, prompts = self._run(
            tmp_path,
            monkeypatch,
            on_invalid="",
            run_id="feedback-run",
            max_retries=1,
            require_prompt_fragment=feedback_header,
        )

        assert result.exit_code == 0, result.stdout
        assert calls == 2
        assert target.exists()
        assert status is not None
        assert status.state == "completed"
        assert feedback_header not in prompts[0]
        assert feedback_header in prompts[1]
        assert 'output: "main"' in prompts[1]
        assert 'kind: "missing"' in prompts[1]
        assert "path:" in prompts[1]
        prompt_files = sorted(
            (target.parent / ".logs" / "tasks" / "write-thing").glob("prompt-*.txt")
        )
        assert len(prompt_files) == 2
        assert sorted(feedback_header in path.read_text() for path in prompt_files) == [False, True]


class TestNonFanOutTransientRetry:
    """A non-fan-out agent step survives a transient failure the way a fan-out item does.

    The week-35 cohorts lost five names to `UND_ERR_BODY_TIMEOUT`: undici gave up
    waiting for the response body, the CLI exited 1 having produced nothing, and
    the scalar path recorded `attempt: 1` and moved on. `run_parallel` retries
    exactly this. These tests pin both halves of the behavior, because a retry
    that cannot tell a body timeout from an exhausted quota is worse than none.
    """

    @staticmethod
    def _write_process(process_dir: Path) -> Path:
        body = textwrap.dedent("""\
            ---
            process:
              name: flaky-exit
              defaults:
                default_adapter: exit-test
                adapters:
                  exit-test:
                    type: exit-test
                retry:
                  max_retries: 3
                  initial_backoff_s: 0
              inputs:
                target_path: { param: TARGET_PATH, as: path }
              steps:
                - id: write-thing
                  mode: agent
                  prompt_prefix: write the thing
                  outputs:
                    main:
                      path: "{{TARGET_PATH}}"
                      kind: file
            ---
            """)
        spec = process_dir / "test.process.md"
        spec.write_text(body)
        return spec

    @staticmethod
    def _register_adapter(
        monkeypatch: pytest.MonkeyPatch,
        counter: Path,
        message: str,
        observed_prompts: list[str],
    ) -> None:
        """An adapter that dies with *message* the first time and succeeds the second."""

        class ExitAdapter:
            adapter_type = "exit-test"
            short_name = "exit-test"
            default_model = None

            def build_command(self, prompt_file, merged_config, variables):  # noqa: ANN001, ARG002
                observed_prompts.append(Path(prompt_file).read_text())
                target_path = variables["TARGET_PATH"]
                script = (
                    "import sys; from pathlib import Path; "
                    f"counter = Path({str(counter)!r}); "
                    "n = len(counter.read_text()) if counter.exists() else 0; "
                    "counter.write_text('x' * (n + 1)); "
                    f"print({message!r}) or sys.exit(1) if n < 1 else None; "
                    f"target = Path({target_path!r}); "
                    "target.parent.mkdir(parents=True, exist_ok=True); "
                    "target.write_text('---\\nstatus: ok\\n---\\n')"
                )
                return [sys.executable, "-c", script]

            def prepare_env(self, env, merged_config):  # noqa: ANN001, ARG002
                return env

            def working_directory(self, merged_config):  # noqa: ANN001, ARG002
                return None

            def parse_result_event(self, line):  # noqa: ANN001, ARG002
                return None

            def check_auth(self):
                raise NotImplementedError

            def auth_info(self):
                return ""

        monkeypatch.setitem(ADAPTER_REGISTRY, "exit-test", ExitAdapter())

    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str, run_id: str):
        repo_dir = tmp_path / "exit-repo"
        process_dir = repo_dir / "exit-process"
        process_dir.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)

        target = repo_dir / "runs" / run_id / "thing.md"
        counter = repo_dir / "counter.txt"
        spec = self._write_process(process_dir)
        observed_prompts: list[str] = []
        self._register_adapter(monkeypatch, counter, message, observed_prompts)

        result = CliRunner().invoke(
            app,
            [
                "run-process",
                str(spec),
                "--var",
                f"RUNS_DIR={repo_dir / 'runs'}",
                "--var",
                f"RUN_ID={run_id}",
                "--var",
                f"TARGET_PATH={target}",
            ],
        )
        status = read_status_at(_task_state_dir_for(repo_dir / "runs" / run_id, "write-thing"))
        calls = len(counter.read_text()) if counter.exists() else 0
        return result, status, calls, target, observed_prompts

    def test_a_body_timeout_is_retried_and_the_second_attempt_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, status, calls, target, prompts = self._run(
            tmp_path, monkeypatch, message="UND_ERR_BODY_TIMEOUT", run_id="transient-run"
        )

        assert result.exit_code == 0, result.stdout
        assert calls == 2, f"the adapter ran {calls}x; a body timeout should be retried once"
        assert target.exists()
        assert status is not None
        assert status.state == "completed"
        assert status.attempt == 2
        history = read_attempt_history_at(_task_state_dir_for(target.parent, "write-thing"))
        assert [record.disposition for record in history] == [
            AttemptDisposition.retryable,
            AttemptDisposition.succeeded,
        ]
        assert all(
            "The prior attempt's declared output failed validation." not in prompt
            for prompt in prompts
        )

    def test_an_exhausted_quota_is_not_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, status, calls, _, _prompts = self._run(
            tmp_path, monkeypatch, message="quota exceeded for this project", run_id="quota-run"
        )

        assert result.exit_code != 0
        assert calls == 1, f"the adapter ran {calls}x; an exhausted quota must not be retried"
        assert status is not None
        assert status.state == "failed"
        assert status.attempt == 1
        # The recorded error carries the log line, not a bare exit code: diagnosing
        # week 35 cost hours precisely because `exit code 1` said nothing.
        assert "quota" in (status.error or "")
