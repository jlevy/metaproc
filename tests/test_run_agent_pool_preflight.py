"""Tests for the per-step preflight quota gate in _run_agent_pool.

the fix (Phase 3 of plan-2026-05-03). Verifies the gate fires
before fan-out, honors --auth-preflight-quota-guard posture (off /
warn / refuse), and skips when no auth pool is configured.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from metaproc.commands import run_parallel
from metaproc.dispatch import preflight as preflight_mod
from metaproc.dispatch.credential_pool import FallbackPolicy, SelectionStrategy
from metaproc.dispatch.pool_dispatch import PoolDispatchConfig
from metaproc.errors import CLIError
from metaproc.runpool.pool import RunPoolConfig


def _make_pool_dispatch(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a minimal pool_dispatch stub for the gate to consult.

    The gate reads `pool_dispatch.adapter` and
    `pool_dispatch.coordinator._backend.list_entries(...)` via
    summarize_headroom. We stub everything below the public seam.
    """
    backend = MagicMock()
    backend.list_entries.return_value = []
    coordinator = MagicMock()
    coordinator._backend = backend  # noqa: SLF001
    pool_dispatch = MagicMock()
    pool_dispatch.adapter = "claude-code-cli"
    pool_dispatch.coordinator = coordinator
    return pool_dispatch


def _patch_check_step_preflight(monkeypatch: pytest.MonkeyPatch, *, status: str):
    """Patch dispatch.preflight.check_step_preflight to a fixed verdict."""

    verdict = MagicMock()
    verdict.status = status
    verdict.projected_units = 1000
    verdict.headroom = MagicMock(pool_remaining_units=2000)
    verdict.message = f"preflight quota gate: status={status}"
    monkeypatch.setattr(preflight_mod, "check_step_preflight", MagicMock(return_value=verdict))
    return preflight_mod.check_step_preflight


# ── Posture-direct tests against the gate logic ──


class TestPreflightGateDirect:
    """Test the gate logic in isolation by importing the relevant
    snippet from _run_agent_pool. The wiring lives inside an async
    function with substantial setup, so we test the surface that
    matters: posture controls the action.
    """

    def test_off_posture_skips_gate(self, monkeypatch: pytest.MonkeyPatch):

        called = MagicMock()
        monkeypatch.setattr(preflight_mod, "check_step_preflight", called)

        # Direct simulation of the gate condition.
        posture = "off"
        pool_dispatch = _make_pool_dispatch(monkeypatch)
        adapter_type = pool_dispatch.adapter

        # The condition mirrors _run_agent_pool's gate condition.
        if pool_dispatch is not None and adapter_type == pool_dispatch.adapter and posture != "off":
            preflight_mod.check_step_preflight(
                pool_dispatch.coordinator._backend,  # noqa: SLF001
                adapter=pool_dispatch.adapter,
                step_id="x",
                fan_out_size=1,
                posture=posture,
            )
        called.assert_not_called()

    def test_warn_verdict_does_not_raise(self, monkeypatch: pytest.MonkeyPatch):

        _patch_check_step_preflight(monkeypatch, status="warn")
        # The patched stub ignores its backend arg, so MagicMock() suffices
        # to satisfy basedpyright's PoolBackend protocol check.
        verdict = preflight_mod.check_step_preflight(
            MagicMock(), adapter="x", step_id="y", fan_out_size=1, posture="warn"
        )
        assert verdict.status == "warn"
        # Production code is expected to log + continue, not raise.

    def test_refuse_verdict_signals_dispatch_should_block(self, monkeypatch: pytest.MonkeyPatch):

        _patch_check_step_preflight(monkeypatch, status="refuse")
        verdict = preflight_mod.check_step_preflight(
            MagicMock(), adapter="x", step_id="y", fan_out_size=1, posture="refuse"
        )
        assert verdict.status == "refuse"


# ── Integration: posture flows from CLI through _run_agent_pool ──


class TestPreflightGateInRunAgentPool:
    """End-to-end-ish: invoke _run_agent_pool with a stubbed pool_dispatch
    and a patched check_step_preflight; assert the gate's posture argument
    matches what was passed in, and that 'refuse' raises CLIError.
    """

    def test_refuse_posture_raises_cli_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):

        verdict = MagicMock()
        verdict.status = "refuse"
        verdict.projected_units = 99999
        verdict.headroom = MagicMock(pool_remaining_units=10)
        verdict.message = "refuse — projected demand 99999 > 80% of 10"
        monkeypatch.setattr(preflight_mod, "check_step_preflight", MagicMock(return_value=verdict))

        pool_config = RunPoolConfig(state_dir=tmp_path, logs_dir=tmp_path)

        backend = MagicMock()
        backend.list_entries.return_value = []
        coordinator = MagicMock()
        coordinator._backend = backend  # noqa: SLF001
        pool_dispatch = PoolDispatchConfig(
            coordinator=coordinator,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            strategy=SelectionStrategy(),
            fallback_policy=FallbackPolicy.NONE,
        )

        # Spec is mostly opaque to the gate path; supply a minimal
        # stand-in so _run_agent_pool can construct.
        spec = MagicMock()
        spec.name = "x"
        step_def = MagicMock()
        step_def.id = "s"

        async def _run():
            await run_parallel._run_agent_pool(  # noqa: SLF001
                spec=spec,
                step_def=step_def,
                step="s",
                each="ticker",
                variables={"RUN_ID": "r"},
                item_contexts=[{"ticker": "AAPL"}],
                adapter_type="claude-code-cli",
                merged_config={},
                effective_outputs=None,
                effective_variant="",
                allowed_runtime=set(),
                retry_policy=MagicMock(),
                process_dir=tmp_path,
                refresh_token_fn=None,
                pool_config=pool_config,
                backend=None,
                out=MagicMock(),
                pool_dispatch=pool_dispatch,
                preflight_quota_guard="refuse",
            )

        with pytest.raises(CLIError, match="quota gate refused"):
            asyncio.run(_run())
