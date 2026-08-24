"""Characterization tests for run_parallel.py — Phase 2a metaproc quality cleanup.

These tests pin the CURRENT behavior of run_parallel.py so that Phase 2b-2e
fixes can be verified by observing which characterization tests change.

Each test documents a specific bug (or recently-fixed bug) and captures the
exact current behavior, whether correct or incorrect.
"""

# pyright: reportIndexIssue=false

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from metaproc.commands.run_parallel import (
    _build_prepare_launch,
    _handle_success,
    _run_agent_pool,
)
from metaproc.engine.placeholders import resolve_runtime_config
from metaproc.engine.retry import RetryVerdict, classify_output_failures
from metaproc.engine.validation import validate_item_outputs_detailed
from metaproc.io.state_io import (
    mark_completed_at,
    mark_failed_at,
    mark_running_at,
    read_attempt_history_at,
    read_result_at,
    read_status_at,
)
from metaproc.models.authored import IOSpec, RetryPolicy
from metaproc.models.runtime import AttemptDisposition, OutputFailure, OutputFailureKind
from metaproc.runpool.pool import RunPoolConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step_def(**overrides: Any) -> Any:
    """Build a minimal mock ProcessStep."""
    step_def = MagicMock()
    step_def.for_each = MagicMock()
    step_def.for_each.item = "ticker"
    step_def.for_each.bind = "ticker"
    step_def.for_each.key = "{{ticker}}"
    step_def.for_each.retry = None
    step_def.outputs = overrides.get("outputs", {})
    step_def.variant = None
    step_def.id = overrides.get("id", "predict")
    step_def.adapter = {"type": "claude-code-cli", "config": {}}
    return step_def


def _make_spec(**overrides: Any) -> Any:
    """Build a minimal mock ProcessSpec."""
    spec = MagicMock()
    spec.name = overrides.get("name", "test-process")
    spec.steps = [_make_step_def()]
    return spec


def _make_shared(item: str = "AAPL", tmp_path: Path | None = None) -> dict[str, Any]:
    """Build the mutable shared dict used by pool submissions."""
    state_dir = (tmp_path / "state" / item) if tmp_path else Path("/tmp/state") / item
    return {
        "item": item,
        "item_context": {"ticker": item},
        "item_vars": {"ticker": item, "VARIANT": "v1"},
        "item_dir": tmp_path / item if tmp_path else None,
        "state_dir": state_dir,
        "running_record": None,
        "log_path": None,
    }


# ===========================================================================
# Bug 1: Code-mode output validation — retry behavior
# ===========================================================================


class TestCodeModeOutputValidationRetry:
    """Pin behavior of code-mode output validation failure retry path.

    BUG DESCRIPTION (Phase 2a): When code-mode execution succeeds (exit_code=0)
    but output validation fails, the code should route through classify_error()
    for retry classification — matching the agent-mode behavior.

    CURRENT STATE: The code-mode path at lines 419-431 DOES call classify_error()
    and retries when the verdict is RETRY. This test pins that behavior.
    """

    def test_code_mode_output_validation_failure_is_classified_from_the_record(
        self,
        tmp_path: Path,
    ) -> None:
        """Code-mode: the retry decision reads the failure record, not the sentence.

        This pins that an output validation failure is still classified rather
        than silently dropped, and that the classifier it reaches is the one
        over ``OutputFailureKind``. If this breaks, the code-mode retry path has
        regressed to substring-matching its own error text.
        """
        item_dir = tmp_path / "AAPL"
        item_dir.mkdir()
        effective_outputs = {"report": IOSpec(path="output.yaml")}

        output_failures = validate_item_outputs_detailed(item_dir, effective_outputs)
        assert output_failures  # validation failed: the file was never written

        assert classify_output_failures(output_failures, effective_outputs) is RetryVerdict.RETRY
        # A declaration on the failing output overrides that default.
        declared = {"report": IOSpec(path="output.yaml", on_invalid={"missing": "fail"})}
        assert classify_output_failures(output_failures, declared) is RetryVerdict.FAIL

    @patch("metaproc.commands.run_parallel.validate_item_outputs_detailed")
    @patch("metaproc.commands.run_parallel.classify_error")
    def test_handle_success_pool_path_routes_validation_failure_to_batch_failed(
        self,
        mock_classify: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Pool path (_handle_success): validation failures go to batch_failed for retry.

        In _handle_success, output validation failures are appended to
        batch_failed, and the outer retry loop classifies them. This test pins
        that the pool path adds validation failures to batch_failed rather than
        silently dropping them, and that it carries the structured record along
        so the verdict is not taken from the sentence.
        """
        mock_validate.return_value = [
            OutputFailure(
                output="report",
                path="output.yaml",
                kind=OutputFailureKind.missing,
                message="file not found",
            )
        ]

        item_dir = tmp_path / "AAPL"
        item_dir.mkdir()

        all_results: list[tuple[str, int]] = []
        batch_failed: list[tuple[dict[str, Any], str, list[OutputFailure]]] = []
        shared = _make_shared("AAPL", tmp_path)
        result = MagicMock()
        result.elapsed_s = 1.0
        out = MagicMock()

        effective_outputs = {"report": IOSpec(path="output.yaml")}

        with patch("metaproc.commands.run_parallel.mark_failed_at"):
            with patch("metaproc.commands.run_parallel.try_compact_log"):
                with patch("metaproc.commands.run_parallel.extract_log_error", return_value=None):
                    _handle_success(
                        each="ticker",
                        item="AAPL",
                        item_dir=item_dir,
                        state_dir=item_dir,
                        item_context={"ticker": "AAPL"},
                        log_path=None,
                        running_record=None,
                        effective_outputs=effective_outputs,
                        variables={"ticker": "AAPL"},
                        run_id="test/run",
                        step="predict",
                        result=result,
                        out=out,
                        all_results=all_results,
                        batch_failed=batch_failed,
                        shared=shared,
                    )

        # Current behavior: validation failure is routed to batch_failed
        assert len(batch_failed) == 1
        assert "output validation failed" in batch_failed[0][1]
        assert [f.kind for f in batch_failed[0][2]] == [OutputFailureKind.missing]
        # NOT added to all_results (retry loop handles that)
        assert len(all_results) == 0

    @patch("metaproc.commands.run_parallel.write_result_at")
    @patch("metaproc.commands.run_parallel.mark_completed_at")
    @patch("metaproc.commands.run_parallel.validate_item_outputs_detailed")
    def test_handle_success_writes_completed_result_with_step_hash(
        self,
        mock_validate: MagicMock,
        mock_mark_completed: MagicMock,
        mock_write_result: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Successful completion writes a ResultRecord with the resolved step hash."""
        mock_validate.return_value = []

        item_dir = tmp_path / "AAPL"
        item_dir.mkdir()
        (item_dir / "output.yaml").write_text("ok: true\n")

        all_results: list[tuple[str, int]] = []
        batch_failed: list[tuple[dict[str, Any], str, list[OutputFailure]]] = []
        shared = _make_shared("AAPL", tmp_path)
        result = MagicMock()
        result.elapsed_s = 1.0
        out = MagicMock()

        effective_outputs = {"report": IOSpec(path="output.yaml")}

        with patch("metaproc.commands.run_parallel.try_compact_log"):
            _handle_success(
                each="ticker",
                item="AAPL",
                item_dir=item_dir,
                state_dir=item_dir,
                item_context={"ticker": "AAPL"},
                log_path=None,
                running_record=None,
                effective_outputs=effective_outputs,
                variables={"ticker": "AAPL"},
                run_id="test/run",
                step="predict",
                result=result,
                out=out,
                all_results=all_results,
                batch_failed=batch_failed,
                shared=shared,
                step_hash="step-hash-123",
            )

        assert batch_failed == []
        assert all_results == [("AAPL", 0)]
        mock_mark_completed.assert_called_once_with(item_dir, running_record=None)
        assert mock_write_result.call_count == 1
        written_record = mock_write_result.call_args.args[1]
        assert written_record.step_hash == "step-hash-123"


# ===========================================================================
# Bug 2: Pool path adapter.prepare_env() call
# ===========================================================================


class TestPoolPrepareEnv:
    """Pin behavior of _build_prepare_launch regarding adapter.prepare_env().

    BUG DESCRIPTION (Phase 2a): The pool path should call adapter.prepare_env()
    to set up adapter-specific environment variables (e.g., API keys).

    CURRENT STATE: _build_prepare_launch at line 932 DOES call
    adapter.prepare_env(). This test pins that behavior.
    """

    @patch("metaproc.commands.run_parallel.get_adapter")
    @patch("metaproc.commands.run_parallel.prepare_step")
    @patch("metaproc.commands.run_parallel.enforce_no_unresolved_placeholders")
    @patch("metaproc.commands.run_parallel.mark_running_at")
    @patch("metaproc.commands.run_parallel.write_attempt_at")
    @patch("metaproc.commands.run_parallel.atomic_output_file")
    def test_build_prepare_launch_calls_prepare_env(
        self,
        mock_atomic: MagicMock,
        mock_write_attempt: MagicMock,
        mock_mark_running: MagicMock,
        mock_enforce: MagicMock,
        mock_prepare_step: MagicMock,
        mock_get_adapter: MagicMock,
        tmp_path: Path,
    ) -> None:
        """_build_prepare_launch's inner _prepare() calls adapter.prepare_env().

        This pins the fix. If prepare_env stops being called,
        adapter-specific env setup (API keys, auth tokens) will be lost.
        """
        mock_adapter = MagicMock()
        mock_adapter.build_command.return_value = ["echo", "hello"]
        mock_adapter.prepare_env.return_value = {"API_KEY": "test123"}
        mock_adapter.working_directory.return_value = None
        mock_get_adapter.return_value = mock_adapter

        log_path = str(tmp_path / "logs" / "AAPL.log")
        mock_prepare_step.return_value = ("prompt text", str(tmp_path / "logs"), log_path)

        # Make atomic_output_file a no-op context manager
        mock_atomic.return_value.__enter__ = MagicMock(return_value=str(tmp_path / "prompt.md"))
        mock_atomic.return_value.__exit__ = MagicMock(return_value=False)

        shared = _make_shared("AAPL", tmp_path)
        item_dir = tmp_path / "AAPL"
        item_dir.mkdir(parents=True, exist_ok=True)
        shared["item_dir"] = item_dir

        prepare_fn = _build_prepare_launch(
            shared=shared,
            item_vars={"ticker": "AAPL", "VARIANT": "v1"},
            item_context={"ticker": "AAPL"},
            attempt=1,
            spec=_make_spec(),
            step_def=_make_step_def(),
            step="predict",
            each="ticker",
            process_dir=tmp_path,
            merged_config={"model": "claude-3", "timeout_s": 300},
            effective_outputs={"report": IOSpec(path="output.yaml")},
            effective_variant="v1",
            allowed_runtime=set(),
            adapter_type="claude-code-cli",
            target_env=None,
            run_id="test/run",
            refresh_token_fn=None,
            pool_status_path=None,
        )

        result = prepare_fn()

        # Current behavior: prepare_env IS called
        mock_adapter.prepare_env.assert_called_once()
        call_args = mock_adapter.prepare_env.call_args
        # First arg is a dict (os.environ copy), second is the item_runtime_config
        assert isinstance(call_args[0][0], dict)

        # The env from prepare_env should be included in the result
        assert result.env is not None
        assert "API_KEY" in result.env

    @patch("metaproc.commands.run_parallel.get_adapter")
    @patch("metaproc.commands.run_parallel.prepare_step")
    @patch("metaproc.commands.run_parallel.enforce_no_unresolved_placeholders")
    @patch("metaproc.commands.run_parallel.mark_running_at")
    @patch("metaproc.commands.run_parallel.write_attempt_at")
    @patch("metaproc.commands.run_parallel.atomic_output_file")
    def test_build_prepare_launch_removes_stale_directory_outputs(
        self,
        mock_atomic: MagicMock,
        mock_write_attempt: MagicMock,
        mock_mark_running: MagicMock,
        mock_enforce: MagicMock,
        mock_prepare_step: MagicMock,
        mock_get_adapter: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Retry cleanup removes stale directory outputs instead of unlinking them."""
        mock_adapter = MagicMock()
        mock_adapter.build_command.return_value = ["echo", "hello"]
        mock_adapter.prepare_env.return_value = {}
        mock_adapter.working_directory.return_value = None
        mock_get_adapter.return_value = mock_adapter

        log_path = str(tmp_path / "logs" / "AAPL.log")
        mock_prepare_step.return_value = ("prompt text", str(tmp_path / "logs"), log_path)
        mock_atomic.return_value.__enter__ = MagicMock(return_value=str(tmp_path / "prompt.md"))
        mock_atomic.return_value.__exit__ = MagicMock(return_value=False)

        shared = _make_shared("AAPL", tmp_path)
        item_dir = tmp_path / "AAPL"
        item_dir.mkdir(parents=True, exist_ok=True)
        shared["item_dir"] = item_dir

        stale_dir = tmp_path / "stale_output"
        stale_dir.mkdir()
        (stale_dir / "child.txt").write_text("stale")

        prepare_fn = _build_prepare_launch(
            shared=shared,
            item_vars={"ticker": "AAPL", "VARIANT": "v1"},
            item_context={"ticker": "AAPL"},
            attempt=1,
            spec=_make_spec(),
            step_def=_make_step_def(),
            step="predict",
            each="ticker",
            process_dir=tmp_path,
            merged_config={"model": "claude-3", "timeout_s": 300},
            effective_outputs={"records": IOSpec(path=str(stale_dir), kind="directory")},
            effective_variant="v1",
            allowed_runtime=set(),
            adapter_type="claude-code-cli",
            target_env=None,
            run_id="test/run",
            refresh_token_fn=None,
            pool_status_path=None,
        )

        prepare_fn()

        assert not stale_dir.exists()

    @patch("metaproc.commands.run_parallel.get_adapter")
    @patch("metaproc.commands.run_parallel.prepare_step")
    @patch("metaproc.commands.run_parallel.enforce_no_unresolved_placeholders")
    @patch("metaproc.commands.run_parallel.mark_running_at")
    @patch("metaproc.commands.run_parallel.write_attempt_at")
    def test_build_prepare_launch_preserves_attempt_prompts_with_structured_feedback(
        self,
        mock_write_attempt: MagicMock,
        mock_mark_running: MagicMock,
        mock_enforce: MagicMock,
        mock_prepare_step: MagicMock,
        mock_get_adapter: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A fan-out retry receives the same structured correction facts as a scalar one."""
        del mock_write_attempt, mock_mark_running
        mock_adapter = MagicMock()
        observed_prompts: list[str] = []
        observed_prompt_paths: list[Path] = []

        def _capture_prompt(prompt_file: Path, *_args: Any) -> list[str]:
            observed_prompt_paths.append(Path(prompt_file))
            observed_prompts.append(Path(prompt_file).read_text())
            return ["echo", "hello"]

        mock_adapter.build_command.side_effect = _capture_prompt
        mock_adapter.prepare_env.return_value = {}
        mock_adapter.working_directory.return_value = None
        mock_get_adapter.return_value = mock_adapter

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        log_path = str(logs_dir / "AAPL.log")
        mock_prepare_step.return_value = ("write the report", str(logs_dir), log_path)

        shared = _make_shared("AAPL", tmp_path)
        failure = OutputFailure(
            output="report",
            path="output.yaml",
            kind=OutputFailureKind.missing,
            message="file not found; literal {{VALIDATOR_DATA}}",
        )
        shared["attempt_number"] = 1
        shared["output_failure_feedback"] = ()

        first_prepare = _build_prepare_launch(
            shared=shared,
            item_vars={"ticker": "AAPL", "VARIANT": "v1"},
            item_context={"ticker": "AAPL"},
            attempt=1,
            spec=_make_spec(),
            step_def=_make_step_def(),
            step="predict",
            each="ticker",
            process_dir=tmp_path,
            merged_config={"model": "claude-3", "timeout_s": 300},
            effective_outputs=None,
            effective_variant="v1",
            allowed_runtime=set(),
            adapter_type="claude-code-cli",
            target_env=None,
            run_id="test/run",
            refresh_token_fn=None,
            pool_status_path=None,
        )
        first_prepare()

        shared["attempt_number"] = 2
        shared["output_failure_feedback"] = (failure,)
        second_prepare = _build_prepare_launch(
            shared=shared,
            item_vars={"ticker": "AAPL", "VARIANT": "v1"},
            item_context={"ticker": "AAPL"},
            attempt=2,
            spec=_make_spec(),
            step_def=_make_step_def(),
            step="predict",
            each="ticker",
            process_dir=tmp_path,
            merged_config={"model": "claude-3", "timeout_s": 300},
            effective_outputs=None,
            effective_variant="v1",
            allowed_runtime=set(),
            adapter_type="claude-code-cli",
            target_env=None,
            run_id="test/run",
            refresh_token_fn=None,
            pool_status_path=None,
        )
        second_prepare()

        assert len(observed_prompts) == 2
        assert observed_prompts[0] == "write the report"
        assert observed_prompts[1].startswith("write the report")
        assert "The prior attempt's declared output failed validation." in observed_prompts[1]
        assert 'output: "report"' in observed_prompts[1]
        assert 'kind: "missing"' in observed_prompts[1]
        assert len(set(observed_prompt_paths)) == 2
        assert all(path.exists() for path in observed_prompt_paths)
        assert mock_enforce.call_count == 2


# ===========================================================================
# Bug 3: Pool timeout uses shared merged_config (NOT per-item resolved)
# ===========================================================================


class TestPoolTimeoutResolution:
    """Pin per-item pool timeout resolution via resolve_runtime_config.

    Pool timeout is now computed per-item using resolve_runtime_config(),
    so template variables in timeout_s are resolved before int() conversion,
    and different items can get different timeouts.
    """

    def test_pool_timeout_resolves_template_per_item(self) -> None:
        """Template variables in timeout_s are resolved before int() conversion."""

        merged_config: dict[str, object] = {"timeout_s": "{{ITEM_TIMEOUT}}"}
        item_vars = {"ITEM_TIMEOUT": "900"}

        resolved = resolve_runtime_config(merged_config, item_vars)
        pool_timeout_s = int(str(resolved["timeout_s"]))
        assert pool_timeout_s == 900

    def test_pool_timeout_differs_per_item(self) -> None:
        """Different items can resolve to different pool timeouts."""

        merged_config: dict[str, object] = {"timeout_s": "{{ITEM_TIMEOUT}}"}

        resolved_a = resolve_runtime_config(merged_config, {"ITEM_TIMEOUT": "300"})
        resolved_b = resolve_runtime_config(merged_config, {"ITEM_TIMEOUT": "900"})

        timeout_a = int(str(resolved_a["timeout_s"]))
        timeout_b = int(str(resolved_b["timeout_s"]))

        assert timeout_a == 300
        assert timeout_b == 900
        assert timeout_a != timeout_b


# ===========================================================================
# Bug 4: pool.submit() exception handling and pool.shutdown() in finally
# ===========================================================================


class TestPoolSubmitAndShutdown:
    """Pin behavior of pool.submit() error handling and shutdown placement.

    BUG DESCRIPTION (Phase 2a): If pool.submit() raises, we should handle it
    gracefully and pool.shutdown() should be in a finally block.

    CURRENT STATE: pool.submit() IS wrapped in try/except (lines 1055-1063)
    and pool.shutdown() IS in a finally block (line 1145-1146). This test
    pins that behavior.
    """

    def test_run_agent_pool_handles_submit_failure(self, tmp_path: Path) -> None:
        """_run_agent_pool catches exceptions from pool.submit().

        Current behavior: submit() failures are caught (line 1055-1063),
        the item is marked as failed, and processing continues to the next
        item. This test verifies that behavior.
        """

        async def _run() -> None:
            mock_pool = MagicMock()
            # submit raises on first call, succeeds on second
            submit_future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
            submit_future.set_result(
                MagicMock(
                    exit_code=0,
                    kill_reason=None,
                    elapsed_s=1.0,
                    backend="mock",
                )
            )
            mock_pool.submit.side_effect = [RuntimeError("pool full"), submit_future]
            mock_pool.snapshot = MagicMock(current_concurrency=2, active_count=0, pending_count=0)

            async def _noop_shutdown():
                pass

            mock_pool.shutdown = _noop_shutdown
            mock_pool._status_path = None  # noqa: SLF001

            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=mock_pool),
                patch(
                    "metaproc.commands.run_parallel._build_prepare_launch", return_value=MagicMock()
                ),
                patch("metaproc.commands.run_parallel.compute_item_dir", return_value=None),
                patch(
                    "metaproc.commands.run_parallel.compute_task_state_dir",
                    side_effect=lambda _run_dir, _step, item_vars: (
                        tmp_path / "state" / item_vars["ticker"]
                    ),
                ),
                patch("metaproc.commands.run_parallel._handle_success"),
            ):
                results = await _run_agent_pool(
                    spec=_make_spec(),
                    step_def=_make_step_def(),
                    step="predict",
                    each="ticker",
                    variables={"VARIANT": "v1"},
                    item_contexts=[{"ticker": "AAPL"}, {"ticker": "GOOGL"}],
                    adapter_type="claude-code-cli",
                    merged_config={"model": "claude-3"},
                    effective_outputs=None,
                    effective_variant="v1",
                    allowed_runtime=set(),
                    retry_policy=RetryPolicy(max_retries=0),
                    process_dir=Path("/tmp/test"),
                    target_env=None,
                    refresh_token_fn=None,
                    pool_config=RunPoolConfig(),
                    backend=MagicMock(),
                    out=MagicMock(),
                )

            # Current behavior: submit failure is caught, item recorded as failed
            # The first item (AAPL) fails with submit error, second (GOOGL) succeeds
            failed = [item for item, code in results if code != 0]
            assert "AAPL" in failed
            history = read_attempt_history_at(tmp_path / "state" / "AAPL")
            assert len(history) == 1
            assert history[0].item_key == "AAPL"
            assert history[0].disposition is AttemptDisposition.permanent
            status = read_status_at(tmp_path / "state" / "AAPL")
            assert status is not None
            assert status.failure_class == history[0].failure_class
            assert status.error == history[0].error

        asyncio.run(_run())

    def test_completed_item_from_another_run_is_not_reused(self, tmp_path: Path) -> None:
        async def _run() -> None:
            class FakePool:
                _status_path = None

                @property
                def snapshot(self) -> Any:
                    return SimpleNamespace(current_concurrency=1, active_count=0, pending_count=0)

                def submit(self, _config: Any) -> asyncio.Future[Any]:
                    raise AssertionError("misaddressed completion must not launch or satisfy task")

                async def shutdown(self) -> None:
                    pass

            state_dir = tmp_path / "AAPL"
            running = mark_running_at(
                state_dir,
                run_id="test-process/another-run",
                step_id="predict",
                item={"ticker": "AAPL"},
                item_key="AAPL",
            )
            mark_completed_at(state_dir, running_record=running)

            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=FakePool()),
                patch(
                    "metaproc.commands.run_parallel.compute_task_state_dir",
                    return_value=state_dir,
                ),
                patch(
                    "metaproc.commands.run_parallel.compute_run_dir",
                    return_value=tmp_path / "run",
                ),
            ):
                with pytest.raises(ValueError, match="run_id"):
                    await _run_agent_pool(
                        spec=_make_spec(),
                        step_def=_make_step_def(),
                        step="predict",
                        each="ticker",
                        variables={"RUN_ID": "current-run", "VARIANT": "v1"},
                        item_contexts=[{"ticker": "AAPL"}],
                        adapter_type="claude-code-cli",
                        merged_config={"model": "claude-3"},
                        effective_outputs=None,
                        effective_variant="v1",
                        allowed_runtime=set(),
                        retry_policy=RetryPolicy(max_retries=0),
                        process_dir=tmp_path,
                        target_env=None,
                        refresh_token_fn=None,
                        pool_config=RunPoolConfig(),
                        backend=MagicMock(),
                        out=MagicMock(),
                    )

        asyncio.run(_run())

    def test_output_feedback_survives_a_transport_retry(self, tmp_path: Path) -> None:
        """Transport failures retain the latest content-failure facts."""

        async def _run() -> None:
            class FakePool:
                _status_path = None

                def __init__(self) -> None:
                    self.submission_count = 0

                @property
                def snapshot(self) -> Any:
                    return SimpleNamespace(current_concurrency=1, active_count=0, pending_count=0)

                def submit(self, _config: Any) -> asyncio.Future[Any]:
                    self.submission_count += 1
                    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                    future.set_result(
                        SimpleNamespace(
                            exit_code=1 if self.submission_count == 2 else 0,
                            kill_reason=None,
                            elapsed_s=0.01,
                        )
                    )
                    return future

                async def shutdown(self) -> None:
                    pass

                def record_failure_class(self, *_args: Any, **_kwargs: Any) -> None:
                    pass

                def record_retry_scheduled(self, *_args: Any, **_kwargs: Any) -> None:
                    pass

                def record_retry_consumed(self, *_args: Any, **_kwargs: Any) -> None:
                    pass

            failure = OutputFailure(
                output="report",
                path="output.yaml",
                kind=OutputFailureKind.missing,
                message="file not found",
            )
            prepared_feedback: list[tuple[OutputFailure, ...]] = []
            completion_count = 0

            def _capture_prepare(*, shared: dict[str, Any], **_kwargs: Any) -> MagicMock:
                prepared_feedback.append(tuple(shared["output_failure_feedback"]))
                return MagicMock()

            def _fake_handle_success(
                _each: str,
                item: str,
                _item_dir: Path | None,
                _state_dir: Path,
                _item_context: dict[str, str],
                _log_path: Path | None,
                _running_record: Any,
                _effective_outputs: dict[str, IOSpec] | None,
                _variables: dict[str, str],
                _run_id: str,
                _step: str,
                _result: Any,
                _out: Any,
                all_results: list[tuple[str, int]],
                batch_failed: list[tuple[dict[str, Any], str, list[OutputFailure]]],
                shared: dict[str, Any],
                **_kwargs: Any,
            ) -> None:
                nonlocal completion_count
                completion_count += 1
                if completion_count == 1:
                    batch_failed.append(
                        (shared, "output validation failed: output.yaml: file not found", [failure])
                    )
                else:
                    all_results.append((item, 0))

            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=FakePool()),
                patch(
                    "metaproc.commands.run_parallel._build_prepare_launch",
                    side_effect=_capture_prepare,
                ),
                patch("metaproc.commands.run_parallel.compute_item_dir", return_value=None),
                patch(
                    "metaproc.commands.run_parallel.compute_task_state_dir",
                    return_value=tmp_path / "state",
                ),
                patch(
                    "metaproc.commands.run_parallel._handle_success",
                    side_effect=_fake_handle_success,
                ),
                patch("metaproc.commands.run_parallel.mark_failed_at"),
            ):
                results = await _run_agent_pool(
                    spec=_make_spec(),
                    step_def=_make_step_def(),
                    step="predict",
                    each="ticker",
                    variables={"VARIANT": "v1"},
                    item_contexts=[{"ticker": "AAPL"}],
                    adapter_type="claude-code-cli",
                    merged_config={"model": "claude-3"},
                    effective_outputs={
                        "report": IOSpec(path="output.yaml", on_invalid={"missing": "retry"})
                    },
                    effective_variant="v1",
                    allowed_runtime=set(),
                    retry_policy=RetryPolicy(max_retries=2, initial_backoff_s=0),
                    process_dir=tmp_path,
                    target_env=None,
                    refresh_token_fn=None,
                    pool_config=RunPoolConfig(),
                    backend=MagicMock(),
                    out=MagicMock(),
                )

            assert results == [("AAPL", 0)]
            assert prepared_feedback == [(), (failure,), (failure,)]

        asyncio.run(_run())

    @pytest.mark.parametrize(
        ("exit_code", "defer_success_transition"),
        [(1, False), (0, True)],
    )
    def test_pool_teardown_failure_does_not_leave_attempt_live(
        self,
        tmp_path: Path,
        exit_code: int,
        defer_success_transition: bool,
    ) -> None:
        async def _run() -> None:
            class FakePool:
                _status_path = None

                @property
                def snapshot(self) -> Any:
                    return SimpleNamespace(current_concurrency=1, active_count=0, pending_count=0)

                def submit(self, config: Any) -> asyncio.Future[Any]:
                    config.resolve_launch()
                    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                    future.set_result(
                        SimpleNamespace(exit_code=exit_code, kill_reason=None, elapsed_s=0.01)
                    )
                    return future

                async def shutdown(self) -> None:
                    pass

                def record_failure_class(self, *_args: Any, **_kwargs: Any) -> None:
                    pass

            state_dir = tmp_path / "state"

            def _prepare(*, shared: dict[str, Any], **_kwargs: Any) -> Any:
                def _mark_running() -> MagicMock:
                    shared["running_record"] = mark_running_at(
                        state_dir,
                        run_id="test-process/test-run",
                        step_id="predict",
                        item={"ticker": "AAPL"},
                        item_key="AAPL",
                    )
                    return MagicMock()

                return _mark_running

            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=FakePool()),
                patch(
                    "metaproc.commands.run_parallel._build_prepare_launch",
                    side_effect=_prepare,
                ),
                patch("metaproc.commands.run_parallel.compute_item_dir", return_value=None),
                patch(
                    "metaproc.commands.run_parallel.compute_task_state_dir",
                    return_value=state_dir,
                ),
                patch(
                    "metaproc.commands.run_parallel._teardown_pool_slot",
                    side_effect=RuntimeError("teardown failed"),
                ),
            ):
                with pytest.raises(RuntimeError, match="teardown failed"):
                    await _run_agent_pool(
                        spec=_make_spec(),
                        step_def=_make_step_def(),
                        step="predict",
                        each="ticker",
                        variables={"RUN_ID": "test-run", "VARIANT": "v1"},
                        item_contexts=[{"ticker": "AAPL"}],
                        adapter_type="claude-code-cli",
                        merged_config={"model": "claude-3"},
                        effective_outputs=None,
                        effective_variant="v1",
                        allowed_runtime=set(),
                        retry_policy=RetryPolicy(max_retries=0),
                        process_dir=tmp_path,
                        target_env=None,
                        refresh_token_fn=None,
                        pool_config=RunPoolConfig(),
                        backend=MagicMock(),
                        out=MagicMock(),
                        pool_dispatch=MagicMock(adapter="claude-code-cli"),
                        preflight_quota_guard="off",
                        defer_success_transition=defer_success_transition,
                    )

            history = read_attempt_history_at(state_dir)
            assert len(history) == 1
            assert history[0].disposition is AttemptDisposition.lost

        asyncio.run(_run())

    def test_outputless_success_finalizes_status_attempt_and_result(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "state"
        running = mark_running_at(
            state_dir,
            run_id="test-process/test-run",
            step_id="notify",
            item={"ticker": "AAPL"},
            item_key="AAPL",
        )
        results: list[tuple[str, int]] = []
        failures: list[tuple[dict[str, Any], str, list[OutputFailure]]] = []

        _handle_success(
            "ticker",
            "AAPL",
            None,
            state_dir,
            {"ticker": "AAPL"},
            None,
            running,
            None,
            {},
            "test-process/test-run",
            "notify",
            SimpleNamespace(elapsed_s=0.01),
            MagicMock(),
            results,
            failures,
            {"attempt_number": 1},
        )

        assert results == [("AAPL", 0)]
        assert failures == []
        status = read_status_at(state_dir)
        assert status is not None
        assert status.state == "completed"
        assert read_result_at(state_dir) is not None
        history = read_attempt_history_at(state_dir)
        assert [record.disposition for record in history] == [AttemptDisposition.succeeded]

    @pytest.mark.parametrize(
        ("exit_code", "kill_reason"),
        [(1, None), (None, "timeout")],
    )
    def test_failed_process_with_valid_outputs_completes_original_attempt(
        self, tmp_path: Path, exit_code: int | None, kill_reason: str | None
    ) -> None:
        async def _run() -> None:
            class FakePool:
                _status_path = None

                @property
                def snapshot(self) -> Any:
                    return SimpleNamespace(current_concurrency=1, active_count=0, pending_count=0)

                def submit(self, config: Any) -> asyncio.Future[Any]:
                    config.resolve_launch()
                    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                    future.set_result(
                        SimpleNamespace(
                            exit_code=exit_code,
                            kill_reason=kill_reason,
                            elapsed_s=0.01,
                        )
                    )
                    return future

                async def shutdown(self) -> None:
                    pass

                def record_failure_class(self, *_args: Any, **_kwargs: Any) -> None:
                    pass

            state_dir = tmp_path / "AAPL"
            output_path = tmp_path / "outputs" / "report.md"

            def _prepare(*, shared: dict[str, Any], **_kwargs: Any) -> Any:
                def _produce_valid_output() -> MagicMock:
                    shared["running_record"] = mark_running_at(
                        state_dir,
                        run_id="test-process/test-run",
                        step_id="predict",
                        item={"ticker": "AAPL"},
                        item_key="AAPL",
                    )
                    output_path.parent.mkdir(parents=True)
                    output_path.write_text("valid\n")
                    return MagicMock()

                return _produce_valid_output

            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=FakePool()),
                patch(
                    "metaproc.commands.run_parallel._build_prepare_launch",
                    side_effect=_prepare,
                ),
                patch(
                    "metaproc.commands.run_parallel.compute_task_state_dir",
                    return_value=state_dir,
                ),
                patch(
                    "metaproc.commands.run_parallel.compute_run_dir",
                    return_value=tmp_path / "run",
                ),
            ):
                results = await _run_agent_pool(
                    spec=_make_spec(),
                    step_def=_make_step_def(),
                    step="predict",
                    each="ticker",
                    variables={"RUN_ID": "test-run", "VARIANT": "v1"},
                    item_contexts=[{"ticker": "AAPL"}],
                    adapter_type="claude-code-cli",
                    merged_config={"model": "claude-3"},
                    effective_outputs={"report": IOSpec(path=str(output_path))},
                    effective_variant="v1",
                    allowed_runtime=set(),
                    retry_policy=RetryPolicy(max_retries=0),
                    process_dir=tmp_path,
                    target_env=None,
                    refresh_token_fn=None,
                    pool_config=RunPoolConfig(),
                    backend=MagicMock(),
                    out=MagicMock(),
                )

            assert results == [("AAPL", 0)]
            history = read_attempt_history_at(state_dir)
            assert [record.disposition for record in history] == [AttemptDisposition.succeeded]

        asyncio.run(_run())

    def test_valid_outputs_do_not_rewrite_terminal_durable_attempt(self, tmp_path: Path) -> None:
        async def _run() -> None:
            class FakePool:
                _status_path = None

                @property
                def snapshot(self) -> Any:
                    return SimpleNamespace(current_concurrency=1, active_count=0, pending_count=0)

                def submit(self, _config: Any) -> asyncio.Future[Any]:
                    raise AssertionError("terminal durable output must not be implicitly adopted")

                async def shutdown(self) -> None:
                    pass

            state_dir = tmp_path / "AAPL"
            output_path = tmp_path / "outputs" / "report.md"
            output_path.parent.mkdir(parents=True)
            output_path.write_text("valid\n")
            running = mark_running_at(
                state_dir,
                run_id="test-process/test-run",
                step_id="predict",
                item={"ticker": "AAPL"},
                item_key="AAPL",
            )
            mark_failed_at(state_dir, error="permanent failure", running_record=running)

            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=FakePool()),
                patch(
                    "metaproc.commands.run_parallel.compute_task_state_dir",
                    return_value=state_dir,
                ),
                patch(
                    "metaproc.commands.run_parallel.compute_run_dir",
                    return_value=tmp_path / "run",
                ),
            ):
                results = await _run_agent_pool(
                    spec=_make_spec(),
                    step_def=_make_step_def(),
                    step="predict",
                    each="ticker",
                    variables={"RUN_ID": "test-run", "VARIANT": "v1"},
                    item_contexts=[{"ticker": "AAPL"}],
                    adapter_type="claude-code-cli",
                    merged_config={"model": "claude-3"},
                    effective_outputs={"report": IOSpec(path=str(output_path))},
                    effective_variant="v1",
                    allowed_runtime=set(),
                    retry_policy=RetryPolicy(max_retries=0),
                    process_dir=tmp_path,
                    target_env=None,
                    refresh_token_fn=None,
                    pool_config=RunPoolConfig(),
                    backend=MagicMock(),
                    out=MagicMock(),
                )

            assert results == [("AAPL", 1)]
            history = read_attempt_history_at(state_dir)
            assert [record.disposition for record in history] == [AttemptDisposition.permanent]

        asyncio.run(_run())

    @pytest.mark.parametrize(
        "prior_disposition",
        [None, AttemptDisposition.lost],
    )
    def test_unaccepted_valid_outputs_are_recomputed(
        self,
        tmp_path: Path,
        prior_disposition: AttemptDisposition | None,
    ) -> None:
        async def _run() -> None:
            class FakePool:
                _status_path = None

                @property
                def snapshot(self) -> Any:
                    return SimpleNamespace(current_concurrency=1, active_count=0, pending_count=0)

                def submit(self, config: Any) -> asyncio.Future[Any]:
                    config.resolve_launch()
                    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                    future.set_result(
                        SimpleNamespace(exit_code=0, kill_reason=None, elapsed_s=0.01)
                    )
                    return future

                async def shutdown(self) -> None:
                    pass

            state_dir = tmp_path / "AAPL"
            output_path = tmp_path / "outputs" / "report.md"
            output_path.parent.mkdir(parents=True)
            output_path.write_text("uncommitted\n")
            if prior_disposition is not None:
                running = mark_running_at(
                    state_dir,
                    run_id="test-process/test-run",
                    step_id="predict",
                    item={"ticker": "AAPL"},
                    item_key="AAPL",
                )
                mark_failed_at(
                    state_dir,
                    error="orchestrator was lost",
                    running_record=running,
                    attempt_disposition=prior_disposition,
                )

            def _prepare(*, shared: dict[str, Any], **_kwargs: Any) -> Any:
                def _mark_recomputed_attempt() -> MagicMock:
                    shared["running_record"] = mark_running_at(
                        state_dir,
                        run_id="test-process/test-run",
                        step_id="predict",
                        item={"ticker": "AAPL"},
                        item_key="AAPL",
                    )
                    output_path.write_text("recomputed\n")
                    return MagicMock()

                return _mark_recomputed_attempt

            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=FakePool()),
                patch(
                    "metaproc.commands.run_parallel._build_prepare_launch",
                    side_effect=_prepare,
                ),
                patch(
                    "metaproc.commands.run_parallel.compute_task_state_dir",
                    return_value=state_dir,
                ),
                patch(
                    "metaproc.commands.run_parallel.compute_run_dir",
                    return_value=tmp_path / "run",
                ),
            ):
                results = await _run_agent_pool(
                    spec=_make_spec(),
                    step_def=_make_step_def(),
                    step="predict",
                    each="ticker",
                    variables={"RUN_ID": "test-run", "VARIANT": "v1"},
                    item_contexts=[{"ticker": "AAPL"}],
                    adapter_type="claude-code-cli",
                    merged_config={"model": "claude-3"},
                    effective_outputs={"report": IOSpec(path=str(output_path))},
                    effective_variant="v1",
                    allowed_runtime=set(),
                    retry_policy=RetryPolicy(max_retries=0),
                    process_dir=tmp_path,
                    target_env=None,
                    refresh_token_fn=None,
                    pool_config=RunPoolConfig(),
                    backend=MagicMock(),
                    out=MagicMock(),
                )

            assert results == [("AAPL", 0)]
            assert output_path.read_text() == "recomputed\n"
            history = read_attempt_history_at(state_dir)
            expected = (
                [AttemptDisposition.succeeded]
                if prior_disposition is None
                else [prior_disposition, AttemptDisposition.succeeded]
            )
            assert [record.disposition for record in history] == expected

        asyncio.run(_run())

    def test_run_agent_pool_refills_after_adaptive_concurrency_increase(self) -> None:
        """The orchestrator periodically fills capacity while active items run."""

        async def _run() -> None:

            loop = asyncio.get_running_loop()
            first_future: asyncio.Future[Any] = loop.create_future()
            submit_labels: list[str] = []

            class FakePool:
                _status_path = None

                @property
                def snapshot(self) -> Any:
                    if not submit_labels:
                        return SimpleNamespace(
                            current_concurrency=1, active_count=0, pending_count=0
                        )
                    if len(submit_labels) == 1:
                        return SimpleNamespace(
                            current_concurrency=2, active_count=1, pending_count=0
                        )
                    return SimpleNamespace(current_concurrency=2, active_count=0, pending_count=0)

                def submit(self, config: Any) -> asyncio.Future[Any]:
                    submit_labels.append(config.label)
                    if len(submit_labels) == 1:
                        return first_future

                    second_future: asyncio.Future[Any] = loop.create_future()
                    result = MagicMock(
                        exit_code=0,
                        kill_reason=None,
                        elapsed_s=1.0,
                        backend="mock",
                    )
                    first_future.set_result(result)
                    second_future.set_result(result)
                    return second_future

                async def shutdown(self) -> None:
                    pass

                def record_failure_class(self, *args: Any, **kwargs: Any) -> None:
                    pass

                def record_retry_scheduled(self, *args: Any, **kwargs: Any) -> None:
                    pass

                def record_retry_consumed(self, *args: Any, **kwargs: Any) -> None:
                    pass

            fake_pool = FakePool()

            def _record_success(
                _each: str,
                item: str,
                _item_dir: Path | None,
                _state_dir: Path,
                _item_context: dict[str, str],
                _log_path: Path | None,
                _running_record: Any,
                _effective_outputs: dict[str, IOSpec] | None,
                _variables: dict[str, str],
                _run_id: str,
                _step: str,
                _result: Any,
                _out: Any,
                all_results: list[tuple[str, int]],
                _batch_failed: list[tuple[dict[str, Any], str, list[OutputFailure]]],
                _shared: dict[str, Any],
                **_kwargs: Any,
            ) -> None:
                all_results.append((item, 0))

            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=fake_pool),
                patch("metaproc.commands.run_parallel._POOL_FILL_POLL_INTERVAL_S", 0.01),
                patch(
                    "metaproc.commands.run_parallel._build_prepare_launch", return_value=MagicMock()
                ),
                patch("metaproc.commands.run_parallel.compute_item_dir", return_value=None),
                patch(
                    "metaproc.commands.run_parallel._handle_success",
                    side_effect=_record_success,
                ),
            ):
                results = await asyncio.wait_for(
                    _run_agent_pool(
                        spec=_make_spec(),
                        step_def=_make_step_def(),
                        step="predict",
                        each="ticker",
                        variables={"VARIANT": "v1"},
                        item_contexts=[{"ticker": "AAPL"}, {"ticker": "GOOGL"}],
                        adapter_type="claude-code-cli",
                        merged_config={"model": "claude-3"},
                        effective_outputs=None,
                        effective_variant="v1",
                        allowed_runtime=set(),
                        retry_policy=RetryPolicy(max_retries=0),
                        process_dir=Path("/tmp/test"),
                        target_env=None,
                        refresh_token_fn=None,
                        pool_config=RunPoolConfig(),
                        backend=MagicMock(),
                        out=MagicMock(),
                    ),
                    timeout=1.0,
                )

            assert submit_labels == ["ticker=AAPL", "ticker=GOOGL"]
            assert sorted(results) == [("AAPL", 0), ("GOOGL", 0)]

        asyncio.run(_run())

    def test_run_agent_pool_shutdown_in_finally(self) -> None:
        """pool.shutdown() is called even when the main loop raises.

        Current behavior: shutdown IS in a finally block (line 1145-1146).
        This test verifies shutdown is called even on unexpected exceptions.
        """

        async def _run() -> None:
            mock_pool = MagicMock()
            mock_pool.submit.side_effect = KeyboardInterrupt("user cancelled")
            mock_pool.snapshot = MagicMock(current_concurrency=2, active_count=0, pending_count=0)
            shutdown_called = asyncio.Event()

            async def mock_shutdown():
                shutdown_called.set()

            mock_pool.shutdown = mock_shutdown
            mock_pool._status_path = None  # noqa: SLF001

            with (
                patch("metaproc.commands.run_parallel.RunPool", return_value=mock_pool),
                patch(
                    "metaproc.commands.run_parallel._build_prepare_launch", return_value=MagicMock()
                ),
                patch("metaproc.commands.run_parallel.compute_item_dir", return_value=None),
            ):
                with pytest.raises(KeyboardInterrupt):
                    await _run_agent_pool(
                        spec=_make_spec(),
                        step_def=_make_step_def(),
                        step="predict",
                        each="ticker",
                        variables={"VARIANT": "v1"},
                        item_contexts=[{"ticker": "AAPL"}],
                        adapter_type="claude-code-cli",
                        merged_config={"model": "claude-3"},
                        effective_outputs=None,
                        effective_variant="v1",
                        allowed_runtime=set(),
                        retry_policy=RetryPolicy(max_retries=0),
                        process_dir=Path("/tmp/test"),
                        target_env=None,
                        refresh_token_fn=None,
                        pool_config=RunPoolConfig(),
                        backend=MagicMock(),
                        out=MagicMock(),
                    )

                # Current behavior: shutdown IS called via finally block
                assert shutdown_called.is_set(), (
                    "pool.shutdown() should be called even when submit raises. "
                    "If this fails, shutdown is not in a finally block."
                )

        asyncio.run(_run())
