"""Credential-pool behavior for scalar agent leaves."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from metaproc import paths as paths_mod
from metaproc.adapters.base import AuthFailureClassification
from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.commands.run_process import RunExecutionContext, _execute_agent_step
from metaproc.dispatch.credential_pool import (
    EntryState,
    FallbackPolicy,
    LocalFilesystemBackend,
    SelectionPolicy,
    SelectionStrategy,
    Vehicle,
    fingerprint_blob,
)
from metaproc.dispatch.pool_dispatch import PoolDispatchConfig, PoolSlotUnavailableError
from metaproc.dispatch.slot_coordinator import SlotCoordinator
from metaproc.engine.pathing import compute_task_state_dir
from metaproc.errors import CLIError
from metaproc.io.state_io import read_attempt_history_at
from metaproc.models.authored import ProcessDefaults, ProcessSpec, ProcessStep, RetryPolicy
from metaproc.models.plan import ResolvedAdapter, ResolvedStep


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
    child_run_id = f"{root_run_id}/child-scope"
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
    run_dir = runs_dir / child_run_id
    observed_label = tmp_path / "observed-label.txt"
    variables = {
        "RUNS_DIR": str(runs_dir),
        "RUN_ID": child_run_id,
        "OBSERVED_LABEL": str(observed_label),
    }
    context = RunExecutionContext.create(
        max_concurrency=1,
        pool_dispatch_template=template,
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
                run_id=child_run_id,
                execution_context=context,
                out=type("Out", (), {"progress": lambda _self, _message: None})(),
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
    assert all(event["run_id"] == child_run_id for event in acquisitions)
    assert all(event["step_id"] == step_def.id for event in acquisitions)
    assert [(event["label"], event["classification"]) for event in outcomes] == [
        ("alt1", "cooling"),
        ("alt2", "ok"),
    ]


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
    )
    run_dir = runs_dir / run_id
    try:
        with pytest.raises(CLIError, match="no eligible pool label"):
            asyncio.run(
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
                    out=type("Out", (), {"progress": lambda _self, _message: None})(),
                )
            )
    finally:
        context.close()

    state_dir = compute_task_state_dir(run_dir, step_def, {})
    assert read_attempt_history_at(state_dir) == ()
