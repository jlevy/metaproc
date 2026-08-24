"""Credential-pool behavior for scalar agent leaves."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from metaproc import paths as paths_mod
from metaproc.adapters.base import AuthFailureClassification
from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.commands.run_process import (
    RunExecutionContext,
    _bind_pool_dispatch,
    _execute_agent_step,
    _execute_composite_step,
    _orchestrate,
)
from metaproc.dispatch.credential_pool import (
    EntryState,
    FallbackPolicy,
    LocalFilesystemBackend,
    SelectionPolicy,
    SelectionStrategy,
    Vehicle,
    fingerprint_blob,
)
from metaproc.dispatch.pool_dispatch import (
    PoolAuthOverrideError,
    PoolDispatchConfig,
    PoolSlotUnavailableError,
    acquire_slot as acquire_pool_slot,
)
from metaproc.dispatch.slot_coordinator import (
    SlotCoordinator,
    SlotLease,
    vehicle_b_label_lock_path,
)
from metaproc.engine.pathing import compute_task_state_dir
from metaproc.errors import CLIError
from metaproc.io import read_yaml_file
from metaproc.io.state_io import read_attempt_history_at, read_status_at
from metaproc.models.authored import ProcessDefaults, ProcessSpec, ProcessStep, RetryPolicy
from metaproc.models.plan import Plan, ResolvedAdapter, ResolvedStep
from metaproc.models.runtime import AttemptDisposition


class _Out:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.warnings: list[str] = []

    def progress(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


class _ScalarAuthAdapter:
    adapter_type = "scalar-auth-test"
    short_name = "scalar-auth-test"
    default_model = None
    slot_credential_filename = "credential.txt"
    compatible_fallback_adapters: list[str] = []  # noqa: RUF012

    def build_command(
        self,
        _prompt_file: Path,
        _merged_config: dict[str, object],
        variables: dict[str, str],
    ) -> list[str]:
        script = (
            "from pathlib import Path; import os, sys; "
            "assert 'TEST_AMBIENT_AUTH' not in os.environ; "
            "label = os.environ['TEST_AUTH_LABEL']; "
            "print('HTTP 429 too_many_requests') if label == 'alt1' else None; "
            "sys.exit(1) if label == 'alt1' else None; "
            f"Path({variables['OBSERVED_LABEL']!r}).write_text(label)"
        )
        return [sys.executable, "-c", script]

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

    def credential_scope_env(
        self, _slot_dir: Path, *, vehicle: object = None, blob: str = ""
    ) -> dict[str, str]:
        del vehicle
        return {"TEST_AUTH_LABEL": blob}

    def credential_scrub_env(self, *, vehicle: object = None) -> dict[str, str]:
        del vehicle
        return {"TEST_AMBIENT_AUTH": ""}

    def materialize_credential(self, slot_dir: Path, blob: str, *, vehicle: object = None) -> None:
        del vehicle
        slot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        (slot_dir / self.slot_credential_filename).write_text(blob)

    def capture_credential(self) -> str:
        return "unused"

    def classify_failure(
        self,
        _exc: BaseException | None,
        _stderr: str,
        session_log_path: Path | None,
    ) -> AuthFailureClassification:
        if (
            session_log_path is not None
            and session_log_path.exists()
            and "too_many_requests" in session_log_path.read_text()
        ):
            return AuthFailureClassification(
                status="cooling",
                cooling_until_ts=2_000_000_000,
                reason="rate-limit",
            )
        return AuthFailureClassification(status="unknown")

    def flush_refreshed_credential(self, _slot_dir: Path) -> str | None:
        return None

    def query_quota_usage(self, _slot_dir: Path) -> None:
        return None

    def query_live_quota(self, _slot_dir: Path, *, vehicle: object = None, blob: str = "") -> None:
        del vehicle, blob

    def debug_capture_args(self, _slot_dir: Path) -> list[str]:
        return []

    def diagnostic_filenames(self) -> tuple[str, ...]:
        return ()

    def setup_token_command(self) -> list[str] | None:
        return None


def test_nested_scalar_agent_uses_selected_pool_label_and_records_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)
    monkeypatch.delenv("TEST_AUTH_LABEL", raising=False)
    monkeypatch.setenv("TEST_AMBIENT_AUTH", "must-be-scrubbed")

    # Model compaction that replaces the original path with a compressed artifact.
    # Classification must consume the sealed log before this transition.
    monkeypatch.setattr(
        "metaproc.commands.run_process.try_compact_log",
        lambda path: path.unlink(missing_ok=True),
    )

    backend = LocalFilesystemBackend(path=tmp_path / "pool" / "credentials.json")
    backend.upsert_entry(
        adapter.adapter_type,
        "alt1",
        blob="alt1",
        state=EntryState(
            status="active",
            fp=fingerprint_blob("alt1"),
            vehicle=Vehicle.OAUTH_TOKEN,
        ),
    )
    backend.upsert_entry(
        adapter.adapter_type,
        "alt2",
        blob="alt2",
        state=EntryState(
            status="active",
            fp=fingerprint_blob("alt2"),
            vehicle=Vehicle.OAUTH_TOKEN,
        ),
    )
    coordinator = SlotCoordinator(backend, adapter_registry={adapter.adapter_type: adapter})
    runs_dir = tmp_path / "runs"
    root_run_id = "root-run"
    scope_id = f"{root_run_id}/child-scope"
    logical_run_id = f"scalar-auth/{scope_id}"
    template = PoolDispatchConfig(
        coordinator=coordinator,
        adapter=adapter.adapter_type,
        runs_dir=runs_dir,
        run_id=root_run_id,
        step="",
        strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1", "alt2")),
    )
    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    spec = ProcessSpec(
        name="scalar-auth",
        defaults=ProcessDefaults(retry=RetryPolicy(max_retries=1, initial_backoff_s=0)),
        steps=[step_def],
    )
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    run_dir = runs_dir / scope_id
    observed_label = tmp_path / "observed-label.txt"
    variables = {
        "RUNS_DIR": str(runs_dir),
        "RUN_ID": scope_id,
        "OBSERVED_LABEL": str(observed_label),
    }
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=template,
        preflight_quota_guard="off",
    )
    try:
        succeeded = asyncio.run(
            _execute_agent_step(
                spec=spec,
                step_def=step_def,
                target=target,
                variables=variables,
                process_dir=tmp_path,
                run_dir=run_dir,
                run_id=logical_run_id,
                execution_context=context,
                out=_Out(),
            )
        )
    finally:
        context.close()

    assert succeeded is True
    assert observed_label.read_text() == "alt2"
    assert os.environ["TEST_AMBIENT_AUTH"] == "must-be-scrubbed"

    event_path = paths_mod.runpool_step_events(run_dir, step_def.id)
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    acquisitions = [event for event in events if event["event"] == "auth_lease_acquired"]
    outcomes = [event for event in events if event["event"] == "auth_outcome"]
    assert [event["label"] for event in acquisitions] == ["alt1", "alt2"]
    assert all(event["run_id"] == scope_id for event in acquisitions)
    assert all(event["step_id"] == step_def.id for event in acquisitions)
    assert all(Path(event["slot_dir"]).is_relative_to(run_dir) for event in acquisitions)
    assert [(event["label"], event["classification"]) for event in outcomes] == [
        ("alt1", "cooling"),
        ("alt2", "ok"),
    ]


def test_composite_executes_scalar_leaf_with_shared_pool_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)
    monkeypatch.delenv("TEST_AUTH_LABEL", raising=False)
    backend = LocalFilesystemBackend(path=tmp_path / "pool" / "credentials.json")
    backend.upsert_entry(
        adapter.adapter_type,
        "alt2",
        blob="alt2",
        state=EntryState(
            status="active",
            fp=fingerprint_blob("alt2"),
            vehicle=Vehicle.OAUTH_TOKEN,
        ),
    )
    coordinator = SlotCoordinator(backend, adapter_registry={adapter.adapter_type: adapter})
    runs_dir = tmp_path / "runs"
    root_scope = "root-run"
    parent_run_dir = runs_dir / root_scope
    child_spec_path = tmp_path / "child.process.md"
    child_spec_path.write_text("---\nprocess:\n  name: child\n  steps: []\n---\n# Child\n")
    child_step = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    child_spec = ProcessSpec(name="child", steps=[child_step])
    child_target = ResolvedStep(
        step_id=child_step.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    monkeypatch.setattr("metaproc.commands.run_process.load_process_spec", lambda _path: child_spec)
    monkeypatch.setattr(
        "metaproc.commands.run_process.expand_process_vars",
        lambda _spec, variables, *, process_dir: variables,
    )
    monkeypatch.setattr(
        "metaproc.commands.run_process.validate_spec_placeholders",
        lambda _spec, _variables: [],
    )
    monkeypatch.setattr(
        "metaproc.commands.run_process.validate_process_inputs",
        lambda _spec, _variables, _process_dir: [],
    )
    monkeypatch.setattr(
        "metaproc.commands.run_process.build_plan",
        lambda *_args, **_kwargs: Plan(process=child_spec.name, steps=[child_target]),
    )
    observed_label = tmp_path / "observed-label.txt"
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=PoolDispatchConfig(
            coordinator=coordinator,
            adapter=adapter.adapter_type,
            runs_dir=runs_dir,
            run_id=root_scope,
            step="",
        ),
        preflight_quota_guard="off",
    )
    out = _Out()
    try:
        succeeded = asyncio.run(
            _execute_composite_step(
                step_def=ProcessStep(
                    id="child-scope",
                    mode="composite",
                    uses=str(child_spec_path),
                ),
                target=ResolvedStep(
                    step_id="child-scope",
                    mode="composite",
                    uses_path=str(child_spec_path),
                ),
                variables={
                    "RUNS_DIR": str(runs_dir),
                    "RUN_ID": root_scope,
                    "OBSERVED_LABEL": str(observed_label),
                },
                process_dir=tmp_path,
                run_dir=parent_run_dir,
                run_id=f"parent/{root_scope}",
                scope_path=(),
                execution_context=context,
                out=out,
            )
        )
    finally:
        context.close()

    child_run_dir = parent_run_dir / "child-scope"
    assert succeeded is True
    assert observed_label.read_text() == "alt2"
    events = [
        json.loads(line)
        for line in paths_mod.runpool_step_events(child_run_dir, child_step.id)
        .read_text()
        .splitlines()
    ]
    acquisition = next(event for event in events if event["event"] == "auth_lease_acquired")
    assert acquisition["run_id"] == f"{root_scope}/child-scope"
    assert Path(acquisition["slot_dir"]).is_relative_to(child_run_dir)


def test_pool_exhaustion_fails_before_scalar_attempt_history_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise PoolSlotUnavailableError(
            adapter.adapter_type,
            policy=FallbackPolicy.NONE,
            excluded=(),
        )

    monkeypatch.setattr("metaproc.commands.run_process.acquire_slot", unavailable)
    runs_dir = tmp_path / "runs"
    run_id = "pool-exhausted"
    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    spec = ProcessSpec(name="scalar-auth", steps=[step_def])
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=PoolDispatchConfig(
            coordinator=MagicMock(),
            adapter=adapter.adapter_type,
            runs_dir=runs_dir,
            run_id=run_id,
            step="",
        ),
        preflight_quota_guard="off",
    )
    run_dir = runs_dir / run_id
    out = _Out()
    try:
        succeeded = asyncio.run(
            _execute_agent_step(
                spec=spec,
                step_def=step_def,
                target=target,
                variables={
                    "RUNS_DIR": str(runs_dir),
                    "RUN_ID": run_id,
                    "OBSERVED_LABEL": str(tmp_path / "never-written.txt"),
                },
                process_dir=tmp_path,
                run_dir=run_dir,
                run_id=run_id,
                execution_context=context,
                out=out,
            )
        )
    finally:
        context.close()

    assert succeeded is False
    assert any("no eligible pool label" in warning for warning in out.warnings)
    state_dir = compute_task_state_dir(run_dir, step_def, {})
    assert read_attempt_history_at(state_dir) == ()


def test_pool_exhaustion_after_retry_marks_existing_attempt_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)
    backend = LocalFilesystemBackend(path=tmp_path / "pool" / "credentials.json")
    backend.upsert_entry(
        adapter.adapter_type,
        "alt1",
        blob="alt1",
        state=EntryState(
            status="active",
            fp=fingerprint_blob("alt1"),
            vehicle=Vehicle.OAUTH_TOKEN,
        ),
    )
    coordinator = SlotCoordinator(backend, adapter_registry={adapter.adapter_type: adapter})
    runs_dir = tmp_path / "runs"
    run_id = "retry-exhausted"
    run_dir = runs_dir / run_id
    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=PoolDispatchConfig(
            coordinator=coordinator,
            adapter=adapter.adapter_type,
            runs_dir=runs_dir,
            run_id=run_id,
            step="",
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1",)),
        ),
        preflight_quota_guard="off",
    )
    try:
        succeeded = asyncio.run(
            _execute_agent_step(
                spec=ProcessSpec(
                    name="scalar-auth",
                    defaults=ProcessDefaults(retry=RetryPolicy(max_retries=1, initial_backoff_s=0)),
                    steps=[step_def],
                ),
                step_def=step_def,
                target=target,
                variables={
                    "RUNS_DIR": str(runs_dir),
                    "RUN_ID": run_id,
                    "OBSERVED_LABEL": str(tmp_path / "never-written.txt"),
                },
                process_dir=tmp_path,
                run_dir=run_dir,
                run_id=f"scalar-auth/{run_id}",
                execution_context=context,
                out=_Out(),
            )
        )
    finally:
        context.close()

    assert succeeded is False
    state_dir = compute_task_state_dir(run_dir, step_def, {})
    history = read_attempt_history_at(state_dir)
    assert len(history) == 1
    assert history[0].disposition is AttemptDisposition.retryable
    status = read_status_at(state_dir)
    assert status is not None
    assert status.state == "failed"
    assert status.attempt == 1
    assert status.error is not None
    assert "no eligible pool label" in status.error


def test_pool_exhaustion_marks_top_level_scalar_step_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise PoolSlotUnavailableError(
            adapter.adapter_type,
            policy=FallbackPolicy.NONE,
            excluded=(),
        )

    monkeypatch.setattr("metaproc.commands.run_process.acquire_slot", unavailable)
    runs_dir = tmp_path / "runs"
    run_id = "pool-exhausted"
    run_dir = runs_dir / run_id
    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    spec = ProcessSpec(name="scalar-auth", steps=[step_def])
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=PoolDispatchConfig(
            coordinator=MagicMock(),
            adapter=adapter.adapter_type,
            runs_dir=runs_dir,
            run_id=run_id,
            step="",
        ),
        preflight_quota_guard="off",
    )
    events = MagicMock()
    try:
        with pytest.raises(CLIError, match="Process completed with failures: scalar-agent"):
            asyncio.run(
                _orchestrate(
                    spec=spec,
                    plan=Plan(process=spec.name, steps=[target]),
                    variables={
                        "RUNS_DIR": str(runs_dir),
                        "RUN_ID": run_id,
                        "OBSERVED_LABEL": str(tmp_path / "never-written.txt"),
                    },
                    process_path=tmp_path / "scalar.process.md",
                    process_dir=tmp_path,
                    run_dir=run_dir,
                    run_id=f"{spec.name}/{run_id}",
                    execution_context=context,
                    out=_Out(),
                    events=events,
                )
            )
    finally:
        context.close()

    status = read_yaml_file(run_dir / ".state" / "process-status.yaml")
    assert status["state"] == "failed"
    assert status["steps"][step_def.id]["state"] == "failed"
    events.step_fail.assert_called_once()
    state_dir = compute_task_state_dir(run_dir, step_def, {})
    assert read_attempt_history_at(state_dir) == ()


def test_pool_adapter_mismatch_is_visible_and_uses_ambient_auth(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    out = _Out()
    template = PoolDispatchConfig(
        coordinator=MagicMock(),
        adapter="claude-code-cli",
        runs_dir=tmp_path / "runs",
        run_id="run",
        step="",
    )

    with caplog.at_level(logging.WARNING, logger="metaproc.commands.run_process"):
        bound = _bind_pool_dispatch(
            template,
            adapter_type="pi-cli",
            run_dir=tmp_path / "runs" / "run",
            step_id="mine",
            out=out,
        )

    expected_warning = (
        "Step 'mine' uses adapter 'pi-cli', but the credential pool is configured for "
        "'claude-code-cli'; the pool is not applied and the step uses its ambient "
        "adapter authentication."
    )
    assert bound is None
    assert out.warnings == [expected_warning]
    assert expected_warning in caplog.messages
    records = [
        json.loads(line)
        for line in paths_mod.runpool_step_events(tmp_path / "runs" / "run", "mine")
        .read_text()
        .splitlines()
    ]
    assert records == [
        {
            "event": "auth_skipped",
            "schema_version": 1,
            "pool_enabled": False,
            "step_id": "mine",
            "step_adapter": "pi-cli",
            "configured_adapter": "claude-code-cli",
            "reason": "adapter_mismatch",
            "ts": records[0]["ts"],
        }
    ]


def test_pool_scope_uses_logical_run_path_when_run_dir_is_a_symlink(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    physical_run_dir = tmp_path / "physical-run"
    physical_run_dir.mkdir()
    logical_run_dir = runs_dir / "linked-run"
    logical_run_dir.symlink_to(physical_run_dir, target_is_directory=True)
    template = PoolDispatchConfig(
        coordinator=MagicMock(),
        adapter="claude-code-cli",
        runs_dir=runs_dir,
        run_id="run",
        step="",
    )

    bound = _bind_pool_dispatch(
        template,
        adapter_type="claude-code-cli",
        run_dir=logical_run_dir,
        step_id="research",
        out=_Out(),
    )

    assert bound is not None
    assert bound.run_id == "linked-run"


def test_scalar_binding_failure_degrades_to_step_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)
    runs_dir = tmp_path / "runs"
    run_dir = tmp_path / "outside" / "run"
    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=PoolDispatchConfig(
            coordinator=MagicMock(),
            adapter=adapter.adapter_type,
            runs_dir=runs_dir,
            run_id="run",
            step="",
        ),
        preflight_quota_guard="off",
    )
    out = _Out()
    try:
        succeeded = asyncio.run(
            _execute_agent_step(
                spec=ProcessSpec(name="scalar-auth", steps=[step_def]),
                step_def=step_def,
                target=target,
                variables={
                    "RUNS_DIR": str(runs_dir),
                    "RUN_ID": "run",
                    "OBSERVED_LABEL": str(tmp_path / "never-written.txt"),
                },
                process_dir=tmp_path,
                run_dir=run_dir,
                run_id="scalar-auth/run",
                execution_context=context,
                out=out,
            )
        )
    finally:
        context.close()

    assert succeeded is False
    assert len(out.warnings) == 1
    assert "outside credential pool runs directory" in out.warnings[0]


def test_pool_scope_must_remain_inside_runs_directory(tmp_path: Path) -> None:
    template = PoolDispatchConfig(
        coordinator=MagicMock(),
        adapter="claude-code-cli",
        runs_dir=tmp_path / "runs",
        run_id="run",
        step="",
    )

    with pytest.raises(CLIError, match="outside credential pool runs directory"):
        _bind_pool_dispatch(
            template,
            adapter_type="claude-code-cli",
            run_dir=tmp_path / "elsewhere" / "run",
            step_id="research",
            out=_Out(),
        )


def test_pool_scope_rejects_parent_traversal_outside_runs_directory(tmp_path: Path) -> None:
    template = PoolDispatchConfig(
        coordinator=MagicMock(),
        adapter="claude-code-cli",
        runs_dir=tmp_path / "runs",
        run_id="run",
        step="",
    )

    with pytest.raises(CLIError, match="outside credential pool runs directory"):
        _bind_pool_dispatch(
            template,
            adapter_type="claude-code-cli",
            run_dir=tmp_path / "runs" / ".." / "elsewhere" / "run",
            step_id="research",
            out=_Out(),
        )


def test_scalar_pool_auth_override_fails_cleanly_and_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)
    monkeypatch.setattr(
        "metaproc.commands.run_process.compose_slot_env",
        MagicMock(side_effect=PoolAuthOverrideError("ambient auth wins")),
    )
    backend = LocalFilesystemBackend(path=tmp_path / "pool" / "credentials.json")
    backend.upsert_entry(
        adapter.adapter_type,
        "alt1",
        blob="alt1",
        state=EntryState(
            status="active",
            fp=fingerprint_blob("alt1"),
            vehicle=Vehicle.OAUTH_TOKEN,
        ),
    )
    coordinator = SlotCoordinator(backend, adapter_registry={adapter.adapter_type: adapter})
    runs_dir = tmp_path / "runs"
    run_id = "override-refused"
    run_dir = runs_dir / run_id
    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=PoolDispatchConfig(
            coordinator=coordinator,
            adapter=adapter.adapter_type,
            runs_dir=runs_dir,
            run_id=run_id,
            step="",
        ),
        preflight_quota_guard="off",
    )
    out = _Out()
    try:
        succeeded = asyncio.run(
            _execute_agent_step(
                spec=ProcessSpec(name="scalar-auth", steps=[step_def]),
                step_def=step_def,
                target=target,
                variables={
                    "RUNS_DIR": str(runs_dir),
                    "RUN_ID": run_id,
                    "OBSERVED_LABEL": str(tmp_path / "never-written.txt"),
                },
                process_dir=tmp_path,
                run_dir=run_dir,
                run_id=f"scalar-auth/{run_id}",
                execution_context=context,
                out=out,
            )
        )
    finally:
        context.close()

    assert succeeded is False
    assert out.warnings == ["Step 'scalar-agent': ambient auth wins"]
    assert coordinator.active_counter.snapshot() == {(adapter.adapter_type, "alt1"): 0}
    state_dir = compute_task_state_dir(run_dir, step_def, {})
    assert read_attempt_history_at(state_dir) == ()


def test_scalar_pool_reuses_quota_preflight_before_acquiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)
    verdict = MagicMock(status="refuse", message="not enough quota")
    preflight = MagicMock(return_value=verdict)
    acquire = MagicMock()
    monkeypatch.setattr("metaproc.commands.run_process.check_step_preflight", preflight)
    monkeypatch.setattr("metaproc.commands.run_process.acquire_slot", acquire)
    runs_dir = tmp_path / "runs"
    run_id = "quota-refused"
    run_dir = runs_dir / run_id
    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    coordinator = MagicMock()
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=PoolDispatchConfig(
            coordinator=coordinator,
            adapter=adapter.adapter_type,
            runs_dir=runs_dir,
            run_id=run_id,
            step="",
        ),
        preflight_quota_guard="refuse",
    )
    out = _Out()
    try:
        succeeded = asyncio.run(
            _execute_agent_step(
                spec=ProcessSpec(name="scalar-auth", steps=[step_def]),
                step_def=step_def,
                target=target,
                variables={
                    "RUNS_DIR": str(runs_dir),
                    "RUN_ID": run_id,
                    "OBSERVED_LABEL": str(tmp_path / "never-written.txt"),
                },
                process_dir=tmp_path,
                run_dir=run_dir,
                run_id=f"scalar-auth/{run_id}",
                execution_context=context,
                out=out,
            )
        )
    finally:
        context.close()

    assert succeeded is False
    preflight.assert_called_once_with(
        coordinator.backend,
        adapter=adapter.adapter_type,
        step_id=step_def.id,
        fan_out_size=1,
        posture="refuse",
    )
    acquire.assert_not_called()
    assert out.warnings == [
        "Step 'scalar-agent': scalar quota gate refused launch: not enough quota"
    ]


def test_scalar_pool_warn_posture_skips_per_step_quota_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)
    preflight = MagicMock()

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise PoolSlotUnavailableError(
            adapter.adapter_type,
            policy=FallbackPolicy.NONE,
            excluded=(),
        )

    monkeypatch.setattr("metaproc.commands.run_process.check_step_preflight", preflight)
    monkeypatch.setattr("metaproc.commands.run_process.acquire_slot", unavailable)
    runs_dir = tmp_path / "runs"
    run_id = "warn-no-scan"
    run_dir = runs_dir / run_id
    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=PoolDispatchConfig(
            coordinator=MagicMock(),
            adapter=adapter.adapter_type,
            runs_dir=runs_dir,
            run_id=run_id,
            step="",
        ),
        preflight_quota_guard="warn",
    )
    try:
        succeeded = asyncio.run(
            _execute_agent_step(
                spec=ProcessSpec(name="scalar-auth", steps=[step_def]),
                step_def=step_def,
                target=target,
                variables={
                    "RUNS_DIR": str(runs_dir),
                    "RUN_ID": run_id,
                    "OBSERVED_LABEL": str(tmp_path / "never-written.txt"),
                },
                process_dir=tmp_path,
                run_dir=run_dir,
                run_id=f"scalar-auth/{run_id}",
                execution_context=context,
                out=_Out(),
            )
        )
    finally:
        context.close()

    assert succeeded is False
    preflight.assert_not_called()


def test_cancellation_during_scalar_credential_acquisition_releases_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _ScalarAuthAdapter()
    monkeypatch.setitem(ADAPTER_REGISTRY, adapter.adapter_type, adapter)
    backend = LocalFilesystemBackend(path=tmp_path / "pool" / "credentials.json")
    backend.upsert_entry(
        adapter.adapter_type,
        "login",
        blob="login",
        state=EntryState(
            status="active",
            fp=fingerprint_blob("login"),
            vehicle=Vehicle.LOGIN_CREDENTIALS,
        ),
    )
    coordinator = SlotCoordinator(backend, adapter_registry={adapter.adapter_type: adapter})
    runs_dir = tmp_path / "runs"
    run_id = "cancel-acquire"
    step_def = ProcessStep(id="scalar-agent", mode="agent", prompt_prefix="test")
    spec = ProcessSpec(name="scalar-auth", steps=[step_def])
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=adapter.adapter_type),
        prompt_prefix="test",
    )
    pool_dispatch = PoolDispatchConfig(
        coordinator=coordinator,
        adapter=adapter.adapter_type,
        runs_dir=runs_dir,
        run_id=run_id,
        step="",
    )
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=pool_dispatch,
        preflight_quota_guard="off",
    )
    acquisition_started = threading.Event()
    allow_acquisition = threading.Event()
    acquisition_finished = threading.Event()
    host_admission_released = asyncio.Event()
    acquired_lease: list[SlotLease] = []

    def delayed_acquire(
        config: PoolDispatchConfig,
        *,
        item: str,
        attempt: int,
        item_exclude: tuple[tuple[str, str], ...] = (),
        session_log_path: Path | None = None,
    ) -> SlotLease:
        acquisition_started.set()
        assert allow_acquisition.wait(timeout=2)
        try:
            lease = acquire_pool_slot(
                config,
                item=item,
                attempt=attempt,
                item_exclude=item_exclude,
                session_log_path=session_log_path,
            )
            acquired_lease.append(lease)
            return lease
        finally:
            acquisition_finished.set()

    @asynccontextmanager
    async def tracked_host_admission(**_kwargs: object):
        try:
            yield object()
        finally:
            host_admission_released.set()

    async def exercise() -> None:
        run_dir = runs_dir / run_id
        with (
            monkeypatch.context() as scoped,
            pytest.raises(asyncio.CancelledError),
        ):
            scoped.setattr("metaproc.commands.run_process.acquire_slot", delayed_acquire)
            scoped.setattr(
                "metaproc.commands.run_process.admitted_launch",
                tracked_host_admission,
            )
            task = asyncio.create_task(
                _execute_agent_step(
                    spec=spec,
                    step_def=step_def,
                    target=target,
                    variables={
                        "RUNS_DIR": str(runs_dir),
                        "RUN_ID": run_id,
                        "OBSERVED_LABEL": str(tmp_path / "never-written.txt"),
                    },
                    process_dir=tmp_path,
                    run_dir=run_dir,
                    run_id=run_id,
                    execution_context=context,
                    out=_Out(),
                )
            )
            assert await asyncio.to_thread(acquisition_started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not host_admission_released.is_set()
            allow_acquisition.set()
            await task

    try:
        asyncio.run(exercise())
        assert acquisition_finished.wait(timeout=2)
        assert not any(coordinator.active_counter.snapshot().values())
        assert not vehicle_b_label_lock_path(adapter.adapter_type, "login").exists()
        assert not list((runs_dir / run_id / ".state" / "auth").rglob("credential.txt"))
        assert context.leaf_semaphore is not None
        assert context.leaf_semaphore._value == 1  # noqa: SLF001 - ownership invariant
    finally:
        allow_acquisition.set()
        acquisition_finished.wait(timeout=2)
        if any(coordinator.active_counter.snapshot().values()) and acquired_lease:
            coordinator.teardown(
                acquired_lease[0],
                failure=AuthFailureClassification(
                    status="unknown",
                    reason="test-cleanup",
                ),
            )
        context.close()
