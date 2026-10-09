"""A provider quota failure backs the pool off and retries the item.

The shape under test: a Gemini CLI agent leaf spends its own internal retries against a
per-minute throughput quota, then exits non-zero with a terminal ``result`` record whose
error reads ``TPM quota exhausted. No spare capacity ...``. The word "quota" made that
failure permanent, the scalar agent path never reported it to the pool, and the provider
ceiling stayed at full width while further leaves failed the same way, so an operator
had to lower the cap by hand and resume the failed items.

Each section below pins one link of that chain: the classification, the pool's
provider ceiling, the scalar agent retry, and the mapped agent retry.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import metaproc.commands.run_process as run_process_module
from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.commands.run_parallel import _run_agent_pool
from metaproc.commands.run_process import (
    RunExecutionContext,
    _execute_agent_step,
    _ScalarLaunchResult,
)
from metaproc.engine.pathing import compute_task_state_dir
from metaproc.engine.retry import (
    INVALID_OUTPUT_RETRY_HEADER,
    QUOTA_RETRY_MIN_BACKOFF_S,
    FailureClass,
    RetryVerdict,
    classify_error,
    classify_failure,
    compute_backoff,
    extract_log_error,
    retry_backoff_s,
)
from metaproc.io.state_io import mark_running_at, read_attempt_history_at
from metaproc.models.authored import IOSpec, ProcessDefaults, ProcessSpec, ProcessStep, RetryPolicy
from metaproc.models.plan import ResolvedAdapter, ResolvedStep
from metaproc.models.runtime import AttemptDisposition
from metaproc.osutils.memory_pressure import PressureLevel
from metaproc.runpool.event_models import ConcurrencyAdjustEvent, QuotaBackoffEvent
from metaproc.runpool.event_reader import read_runpool_events
from metaproc.runpool.pool import RunPool, RunPoolConfig

# The terminal record Gemini CLI writes after its own retries against a Vertex per-minute
# quota are exhausted. The message is long enough that ``extract_log_error`` truncates it,
# which is part of what the classifier has to cope with.
GEMINI_TPM_QUOTA_MESSAGE = (
    "[API Error: TPM quota exhausted. No spare capacity is currently available to serve "
    "over-limit requests. Please try again later, reduce your request rate or request an "
    "increase to your PayGo TPM limit. Details: "
    "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/standard-paygo.]"
    "\nPlease wait and try again later. To increase your limits, request a quota increase "
    "through Vertex, or switch to another /auth method"
)


def _quota_transcript() -> str:
    """A Gemini stream that ends in the per-minute quota refusal."""
    return (
        "\n".join(
            [
                json.dumps({"type": "init", "model": "gemini-test"}),
                json.dumps({"type": "message", "role": "assistant", "content": "working"}),
                (
                    "Attempt 10 failed: TPM quota exhausted. No spare capacity is currently "
                    "available to serve over-limit requests."
                ),
                json.dumps(
                    {
                        "type": "result",
                        "status": "error",
                        "error": {"type": "unknown", "message": GEMINI_TPM_QUOTA_MESSAGE},
                        "stats": {"total_tokens": 1000, "input_tokens": 900, "tool_calls": 3},
                    }
                ),
            ]
        )
        + "\n"
    )


def _exit_error_from_transcript(tmp_path: Path, exit_code: int = 173) -> str:
    """Build the error string exactly as both agent paths build it from a log."""
    log = tmp_path / "quota.jsonl"
    log.write_text(_quota_transcript(), encoding="utf-8")
    log_error = extract_log_error(log)
    assert log_error is not None
    return f"exit code {exit_code} (log: {log_error})"


# ── Classification ────────────────────────────────────────────────────────


class TestRateQuotaClassification:
    """A per-minute quota refusal is a quota failure that another attempt can clear."""

    def test_gemini_tpm_quota_exit_is_retryable_quota_exhaustion(self, tmp_path: Path) -> None:
        error = _exit_error_from_transcript(tmp_path)
        assert classify_failure(error) == FailureClass.QUOTA_EXHAUSTED
        assert classify_error(error) == RetryVerdict.RETRY

    @pytest.mark.parametrize(
        "error",
        [
            (
                "RetryableQuotaError: Quota exceeded for quota metric 'Generate Content "
                "requests per minute'"
            ),
            "exit code 1 (log: rpm quota exceeded for this project)",
            "exit code 1 (log: quota exceeded: input tokens per minute)",
            (
                "Quota exceeded for aiplatform.googleapis.com/"
                "generate_content_requests_per_minute_per_project_per_base_model"
            ),
        ],
    )
    def test_other_per_minute_quota_wordings_are_retryable(self, error: str) -> None:
        assert classify_failure(error) == FailureClass.QUOTA_EXHAUSTED
        assert classify_error(error) == RetryVerdict.RETRY

    @pytest.mark.parametrize(
        "error",
        [
            # A cap that lifts on a day or a month is not a retry-timescale failure,
            # even when the message also names a per-minute limit.
            "exit code 1 (log: TPM quota exhausted; daily quota also exhausted)",
            "TerminalQuotaError: You have exhausted your daily quota on this model",
            "Quota exceeded: generate_content_requests_per_minute and requests_per_day",
            "You've hit your org's monthly usage limit (tokens per minute quota)",
            "TPM quota exhausted; billing account is disabled",
            # Unrelated permanent signals keep priority over the quota exemption.
            "exit code 137 (log: TPM quota exhausted. No spare capacity is available)",
            "exit code 143 (log: TPM quota exhausted. No spare capacity is available)",
            # A quota failure with no per-minute marker keeps its permanent verdict.
            "You exceeded your current quota",
            "API quota exceeded",
        ],
    )
    def test_hard_quota_failures_stay_permanent(self, error: str) -> None:
        assert classify_error(error) == RetryVerdict.FAIL


class TestQuotaRetryBackoff:
    def test_quota_retry_waits_at_least_one_quota_window(self) -> None:
        policy = RetryPolicy(max_retries=3, initial_backoff_s=5.0)
        assert compute_backoff(1, policy) == 5.0
        assert retry_backoff_s(FailureClass.QUOTA_EXHAUSTED, 1, policy) == (
            QUOTA_RETRY_MIN_BACKOFF_S
        )

    def test_a_longer_policy_backoff_is_kept(self) -> None:
        policy = RetryPolicy(max_retries=3, initial_backoff_s=QUOTA_RETRY_MIN_BACKOFF_S * 2)
        assert retry_backoff_s(FailureClass.QUOTA_EXHAUSTED, 1, policy) == (
            QUOTA_RETRY_MIN_BACKOFF_S * 2
        )

    @pytest.mark.parametrize(
        "failure_class",
        [FailureClass.RATE_LIMITED, FailureClass.SERVER_ERROR, FailureClass.CRASH],
    )
    def test_other_classes_keep_the_policy_backoff(self, failure_class: FailureClass) -> None:

        policy = RetryPolicy(max_retries=3, initial_backoff_s=5.0)
        assert retry_backoff_s(failure_class, 2, policy) == compute_backoff(2, policy)


# ── Provider ceiling ──────────────────────────────────────────────────────


def _pool(tmp_path: Path, **overrides: Any) -> RunPool:
    config: dict[str, Any] = {
        "max_concurrency": 30,
        "initial_concurrency": 30,
        "min_concurrency": 1,
        "pressure_check_interval_s": 60.0,
        "hysteresis_checks": 3,
        "state_dir": tmp_path / "state",
        "logs_dir": tmp_path / "logs",
    }
    config.update(overrides)
    return RunPool(RunPoolConfig(**config))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    now = {"value": 1_000.0}
    monkeypatch.setattr("metaproc.runpool.pool.time.monotonic", lambda: now["value"])
    return now


class TestQuotaBackoffGovernor:
    def test_one_quota_failure_halves_the_provider_ceiling(self, tmp_path: Path) -> None:
        """No burst threshold: the agent already retried internally before failing."""
        pool = _pool(tmp_path)
        assert pool.current_max_concurrency == 30

        pool.record_failure_class("quota_exhausted")

        status = pool.snapshot
        assert pool.current_max_concurrency == 15
        assert status.controller is not None
        assert status.controller.provider_ceiling == 15
        assert status.controller.memory_ceiling == 30
        assert status.controller.bottleneck == "DSQ-bound"

    def test_failures_admitted_before_the_cut_do_not_cut_again(
        self, tmp_path: Path, clock: dict[str, float]
    ) -> None:
        """One saturation episode surfaces as several failures over a few minutes.

        Every process that was already running when the ceiling fell was admitted under
        the old ceiling, so its failure says nothing about the new one.
        """
        pool = _pool(tmp_path)
        pool.record_failure_class("quota_exhausted", elapsed_s=400.0)
        assert pool.current_max_concurrency == 15

        clock["value"] += 70.0
        pool.record_failure_class("quota_exhausted", elapsed_s=420.0)
        clock["value"] += 100.0
        pool.record_failure_class("quota_exhausted", elapsed_s=580.0)
        assert pool.current_max_concurrency == 15

    def test_a_failure_admitted_after_the_cut_cuts_again(
        self, tmp_path: Path, clock: dict[str, float]
    ) -> None:
        pool = _pool(tmp_path)
        pool.record_failure_class("quota_exhausted", elapsed_s=400.0)
        assert pool.current_max_concurrency == 15

        clock["value"] += 200.0
        # Launched 100s after the cut, under the reduced ceiling, and still refused.
        pool.record_failure_class("quota_exhausted", elapsed_s=100.0)
        assert pool.current_max_concurrency == 7

    def test_an_unknown_launch_time_holds_without_cutting_again(
        self, tmp_path: Path, clock: dict[str, float]
    ) -> None:
        pool = _pool(tmp_path)
        pool.record_failure_class("quota_exhausted")
        clock["value"] += 10.0
        pool.record_failure_class("quota_exhausted")
        assert pool.current_max_concurrency == 15

    def test_the_ceiling_never_falls_below_min_concurrency(
        self, tmp_path: Path, clock: dict[str, float]
    ) -> None:
        pool = _pool(tmp_path, initial_concurrency=2, min_concurrency=1)
        pool.record_failure_class("quota_exhausted", elapsed_s=1.0)
        assert pool.current_max_concurrency == 1
        clock["value"] += 10.0
        pool.record_failure_class("quota_exhausted", elapsed_s=1.0)
        assert pool.current_max_concurrency == 1

    def test_recovery_waits_out_the_hold_then_ramps_gradually(
        self, tmp_path: Path, clock: dict[str, float]
    ) -> None:
        pool = _pool(tmp_path, quota_backoff_hold_s=300.0)
        pool.record_failure_class("quota_exhausted")
        assert pool.current_max_concurrency == 15

        for _ in range(6):
            clock["value"] += 10.0
            pool._adjust_concurrency(PressureLevel.NORMAL)
        assert pool.current_max_concurrency == 15

        clock["value"] = 1_301.0
        for _ in range(3):
            pool._adjust_concurrency(PressureLevel.NORMAL)
        # The ordinary provider recovery step: +10%, at least one slot.
        assert pool.current_max_concurrency == 16

    def test_a_later_failure_extends_the_hold(
        self, tmp_path: Path, clock: dict[str, float]
    ) -> None:
        pool = _pool(tmp_path, quota_backoff_hold_s=300.0)
        pool.record_failure_class("quota_exhausted", elapsed_s=400.0)
        clock["value"] = 1_200.0
        pool.record_failure_class("quota_exhausted", elapsed_s=500.0)

        clock["value"] = 1_400.0
        for _ in range(3):
            pool._adjust_concurrency(PressureLevel.NORMAL)
        assert pool.current_max_concurrency == 15

        clock["value"] = 1_501.0
        for _ in range(3):
            pool._adjust_concurrency(PressureLevel.NORMAL)
        assert pool.current_max_concurrency == 16

    def test_a_reset_clock_quota_still_pauses(self, tmp_path: Path) -> None:
        """The reset-clock pause is unchanged; the ceiling cut applies alongside it."""

        async def _run() -> None:
            pool = _pool(tmp_path)
            reset_at = datetime.now(UTC) + timedelta(hours=1)
            try:
                pool.record_failure_class("quota_exhausted", quota_reset_at=reset_at)
                assert pool._quota_paused_until == reset_at
                assert pool.current_max_concurrency == 15
            finally:
                if pool._quota_pause_task is not None:
                    pool._quota_pause_task.cancel()
                await pool.shutdown()

        asyncio.run(_run())

    def test_the_backoff_is_visible_in_the_event_log(self, tmp_path: Path) -> None:
        pool = _pool(tmp_path)
        assert pool._event_logger is not None
        pool._event_logger.open()
        try:
            pool.record_failure_class("quota_exhausted", elapsed_s=30.0)
            pool.record_failure_class("quota_exhausted", elapsed_s=300.0)
        finally:
            pool._event_logger.close()

        events = read_runpool_events(tmp_path / "logs" / "events.jsonl")
        backoffs = [event for event in events if isinstance(event, QuotaBackoffEvent)]
        assert [(e.action, e.old_provider_ceiling, e.provider_ceiling) for e in backoffs] == [
            ("cut", 30, 15),
            ("hold", 15, 15),
        ]
        adjusts = [event for event in events if isinstance(event, ConcurrencyAdjustEvent)]
        assert [(e.old, e.new, e.reason) for e in adjusts] == [(30, 15, "quota_backoff")]

    def test_other_failure_classes_leave_the_provider_ceiling_alone(self, tmp_path: Path) -> None:
        pool = _pool(tmp_path)
        for failure_class in ("timeout", "server_error", "crash", "invalid_output", "unknown"):
            pool.record_failure_class(failure_class)
        assert pool.current_max_concurrency == 30


# ── Scalar agent step ─────────────────────────────────────────────────────

SCALAR_ADAPTER = "quota-backoff-test"


class _Out:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.warnings: list[str] = []

    def progress(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


class _StubAdapter:
    """Never launched: ``_run_scalar_agent_subprocess`` is replaced in each test."""

    adapter_type = SCALAR_ADAPTER
    short_name = SCALAR_ADAPTER
    default_model = None
    slot_credential_filename = "credential.txt"
    compatible_fallback_adapters: list[str] = []  # noqa: RUF012

    def build_command(
        self, _prompt_file: Path, _merged_config: dict[str, object], _variables: dict[str, str]
    ) -> list[str]:
        return ["true"]

    def prepare_env(self, env: dict[str, str], _merged_config: dict[str, object]) -> dict[str, str]:
        return env

    def working_directory(self, _merged_config: dict[str, object]) -> Path | None:
        return None

    def parse_result_event(self, _line: str) -> dict[str, object] | None:
        return None

    def check_auth(self) -> object:
        return object()

    def auth_info(self) -> str:
        return ""

    def validate_config(self, _merged_config: dict[str, object]) -> list[object]:
        return []

    def bootstrap(self, _home: Path) -> None:
        return None


class _RecordedSleep:
    """Stand in for ``asyncio`` inside ``run_process`` so backoff is recorded, not slept."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    async def sleep(self, delay: float, result: Any = None) -> Any:
        self.delays.append(delay)
        return result


def _scalar_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcomes: list[str],
    max_retries: int,
) -> SimpleNamespace:
    """Run one scalar agent step whose attempts end as *outcomes* say."""
    monkeypatch.setitem(ADAPTER_REGISTRY, SCALAR_ADAPTER, _StubAdapter())
    runs_dir = tmp_path / "runs"
    run_id = "quota-run"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = run_dir / "summary.md"
    outputs = {"summary": IOSpec(path="{{run.dir}}/summary.md", kind="file")}
    launches: list[Path] = []

    async def _fake_launch(*_args: Any, **kwargs: Any) -> _ScalarLaunchResult:
        log_path = Path(kwargs["log_path"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        launches.append(log_path)
        outcome = outcomes[len(launches) - 1]
        if outcome == "quota":
            log_path.write_text(_quota_transcript(), encoding="utf-8")
            # No real time passes here, so a zero run time places each launch after
            # any cut an earlier attempt caused, as a relaunch under backoff would be.
            return _ScalarLaunchResult(exit_code=173, kill_reason=None, elapsed_s=0.0)
        log_path.write_text(
            json.dumps({"type": "result", "status": "success"}) + "\n", encoding="utf-8"
        )
        summary.write_text("# summary\n", encoding="utf-8")
        return _ScalarLaunchResult(exit_code=0, kill_reason=None, elapsed_s=0.0)

    monkeypatch.setattr(run_process_module, "_run_scalar_agent_subprocess", _fake_launch)
    sleeper = _RecordedSleep()
    monkeypatch.setattr(run_process_module, "asyncio", sleeper)

    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test", outputs=outputs)
    spec = ProcessSpec(
        name="quota-backoff",
        defaults=ProcessDefaults(retry=RetryPolicy(max_retries=max_retries, initial_backoff_s=0)),
        steps=[step_def],
    )
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=SCALAR_ADAPTER),
        prompt_prefix="test",
        outputs=outputs,
    )
    variables = {"RUNS_DIR": str(runs_dir), "RUN_ID": run_id}
    out = _Out()
    pools: list[RunPool] = []

    def _agent_pool(_self: RunExecutionContext, **_kwargs: Any) -> RunPool:
        return pools[0]

    monkeypatch.setattr(RunExecutionContext, "agent_pool", _agent_pool)

    async def _run() -> bool:
        pools.append(_pool(tmp_path / "pool"))
        context = RunExecutionContext.create(max_concurrency=None)
        try:
            return await _execute_agent_step(
                spec=spec,
                step_def=step_def,
                target=target,
                variables=variables,
                process_dir=tmp_path,
                run_dir=run_dir,
                run_id=run_id,
                execution_context=context,
                out=out,
            )
        finally:
            context.close()
            await pools[0].shutdown()

    succeeded = asyncio.run(_run())
    state_dir = compute_task_state_dir(run_dir, step_def, {**variables, "VARIANT": ""})
    prompts = sorted(
        launches[0].parent.glob("prompt-scalar-agent-attempt*.txt"),
        key=lambda path: path.name,
    )
    return SimpleNamespace(
        succeeded=succeeded,
        history=read_attempt_history_at(state_dir),
        pool=pools[0],
        delays=sleeper.delays,
        prompts=prompts,
        launches=launches,
        out=out,
    )


class TestScalarAgentQuotaRetry:
    def test_a_quota_refusal_is_retried_after_backoff_and_lowers_the_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _scalar_run(tmp_path, monkeypatch, outcomes=["quota", "success"], max_retries=2)

        assert result.succeeded is True
        assert [row.disposition for row in result.history] == [
            AttemptDisposition.retryable,
            AttemptDisposition.succeeded,
        ]
        assert result.history[0].failure_class == "quota_exhausted"
        assert result.pool.current_max_concurrency == 15
        assert result.delays == [QUOTA_RETRY_MIN_BACKOFF_S]

    def test_a_quota_retry_is_not_a_content_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The retry reruns the authored prompt; nothing is appended as correction."""
        result = _scalar_run(tmp_path, monkeypatch, outcomes=["quota", "success"], max_retries=2)

        assert len(result.prompts) == 2
        retry_prompt = result.prompts[1].read_text(encoding="utf-8")
        assert INVALID_OUTPUT_RETRY_HEADER not in retry_prompt
        assert retry_prompt == result.prompts[0].read_text(encoding="utf-8")

    def test_quota_retries_stop_at_the_declared_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _scalar_run(
            tmp_path, monkeypatch, outcomes=["quota", "quota", "quota"], max_retries=1
        )

        assert result.succeeded is False
        assert len(result.launches) == 2
        # Retries ran out, not a permanent verdict: a resume may run it again.
        assert [row.disposition for row in result.history] == [
            AttemptDisposition.retryable,
            AttemptDisposition.retryable,
        ]
        assert {row.failure_class for row in result.history} == {"quota_exhausted"}
        # The second refusal came from a launch admitted under the lowered ceiling.
        assert result.pool.current_max_concurrency == 7

    def test_no_retry_budget_means_no_automatic_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A step declaring no retries makes no hidden extra calls."""
        result = _scalar_run(tmp_path, monkeypatch, outcomes=["quota", "success"], max_retries=0)

        assert result.succeeded is False
        assert len(result.launches) == 1
        assert result.delays == []
        # The pool still learns about the quota, which costs nothing.
        assert result.pool.current_max_concurrency == 15


# ── Mapped agent step ─────────────────────────────────────────────────────


class TestMappedAgentQuotaRetry:
    def test_a_quota_refusal_is_rescheduled_with_backoff_and_reported_to_the_pool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("metaproc.commands.run_parallel._POOL_FILL_POLL_INTERVAL_S", 0.01)
        monkeypatch.setattr("metaproc.engine.retry.QUOTA_RETRY_MIN_BACKOFF_S", 0.05, raising=False)
        state_dir = tmp_path / "state" / "synthetic-a"
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        attempts: list[int] = []
        scheduled: list[tuple[int, float]] = []
        recorded: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        outcomes = ["quota", "success"]

        def _prepare(*, shared: dict[str, Any], **kwargs: Any) -> MagicMock:
            log_path = logs_dir / f"attempt-{shared['attempt_number']}.jsonl"
            if outcomes[shared["attempt_number"] - 1] == "quota":
                log_path.write_text(_quota_transcript(), encoding="utf-8")
            else:
                log_path.write_text(json.dumps({"type": "message"}) + "\n", encoding="utf-8")
            shared["log_path"] = log_path
            shared["running_record"] = mark_running_at(
                state_dir,
                run_id=str(kwargs["run_id"]),
                step_id="author",
                item={"item": "synthetic-a"},
                item_key="synthetic-a",
                attempt=shared["attempt_number"],
            )
            return MagicMock()

        class FakePool:
            _status_path = None
            snapshot = SimpleNamespace(current_concurrency=1, active_count=0, pending_count=0)

            def submit(self, config: Any) -> asyncio.Future[Any]:
                shared = config.metadata["shared"]
                attempts.append(shared["attempt_number"])
                outcome = outcomes[shared["attempt_number"] - 1]
                future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                future.set_result(
                    SimpleNamespace(
                        exit_code=173 if outcome == "quota" else 0,
                        kill_reason=None,
                        elapsed_s=397.0,
                    )
                )
                return future

            async def shutdown(self) -> None:
                pass

            def record_failure_class(self, *args: Any, **kwargs: Any) -> None:
                recorded.append((args, kwargs))

            def record_retry_scheduled(self, _label: str, attempt: int, delay: float) -> None:
                scheduled.append((attempt, delay))

            def record_retry_consumed(self, *_args: Any, **_kwargs: Any) -> None:
                pass

        step_def = MagicMock()
        step_def.for_each = MagicMock()
        step_def.for_each.item = "item"
        step_def.for_each.bind = "item"
        step_def.for_each.key = "{{item}}"
        step_def.for_each.retry = None
        step_def.outputs = {}
        step_def.variant = None
        step_def.id = "author"
        step_def.adapter = {"type": "gemini-cli", "config": {}}
        spec = MagicMock()
        spec.name = "quota-backoff"
        spec.steps = [step_def]

        async def _run() -> list[tuple[str, int]]:
            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=FakePool()),
                patch("metaproc.commands.run_parallel._build_prepare_launch", side_effect=_prepare),
                patch("metaproc.commands.run_parallel.compute_item_dir", return_value=None),
                patch(
                    "metaproc.commands.run_parallel.compute_task_state_dir",
                    return_value=state_dir,
                ),
                patch("metaproc.commands.run_parallel._teardown_pool_slot", return_value=None),
            ):
                return await _run_agent_pool(
                    spec=spec,
                    step_def=step_def,
                    step="author",
                    each="item",
                    variables={"RUN_ID": "test/run", "VARIANT": "v1"},
                    item_contexts=[{"item": "synthetic-a"}],
                    adapter_type="gemini-cli",
                    merged_config={"model": "gemini-test"},
                    effective_outputs=None,
                    effective_variant="v1",
                    allowed_runtime=set(),
                    retry_policy=RetryPolicy(max_retries=2, initial_backoff_s=0),
                    process_dir=tmp_path,
                    target_env=None,
                    refresh_token_fn=None,
                    pool_config=RunPoolConfig(),
                    backend=MagicMock(),
                    out=MagicMock(),
                    pool_dispatch=None,
                    preflight_quota_guard="off",
                )

        started = time.monotonic()
        results = asyncio.run(_run())

        assert results == [("synthetic-a", 0)]
        assert attempts == [1, 2]
        assert scheduled == [(2, 0.05)]
        assert time.monotonic() - started >= 0.05
        assert recorded == [(("quota_exhausted",), {"quota_reset_at": None, "elapsed_s": 397.0})]
        history = read_attempt_history_at(state_dir)
        assert [row.disposition for row in history] == [
            AttemptDisposition.retryable,
            AttemptDisposition.succeeded,
        ]
        assert history[0].failure_class == "quota_exhausted"
