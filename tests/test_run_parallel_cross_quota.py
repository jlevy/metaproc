"""Tests for run-parallel's --auth-cross-quota-group flag.

The flag was added so cloud workers (which run inner ``run-parallel
--backend local``) can honor the operator's diagnostic cross-quota
opt-out instead of crashing on an unknown option or silently dropping
it. The propagation chain is:

    operator --no-auth-cross-quota-group on run-process
        ↓ AuthPoolFlags.to_cli_flags adds --no-auth-cross-quota-group
        ↓ worker_entrypoint rebuilds run-parallel command
    run-parallel --no-auth-cross-quota-group
        ↓ _build_pool_dispatch_template
    PoolDispatchConfig(cross_quota_group=False)
        ↓ pool_dispatch.acquire_slot
    SlotCoordinator (cross-quota fallback disabled for the dispatch)

This test asserts the third arrow.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

from metaproc.commands.run_parallel import (
    _build_pool_dispatch_template,
    _filter_pool_dispatch_for_adapter,
)
from metaproc.errors import CLIError


@pytest.fixture
def common_kwargs(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    monkeypatch.delenv("METAPROC_GCP_PROJECT", raising=False)
    monkeypatch.delenv("METAPROC_AUTH_POOL", raising=False)
    return {
        "auth_account": "claude-code-cli",
        "auth_backend": "local",
        "auth_fallback_policy": "same-provider",
        "auth_include_labels": ["alt1"],
        "auth_exclude_labels": [],
        "backend_name": "local",
        "run_context": "test-run",
        "out": MagicMock(),
    }


class TestCrossQuotaGroupPropagation:
    def test_default_true_lands_on_pool_dispatch_config(self, common_kwargs):
        template = _build_pool_dispatch_template(
            **common_kwargs,
            auth_cross_quota_group=True,
        )
        assert template is not None
        assert template.cross_quota_group is True

    def test_explicit_false_propagates(self, common_kwargs):
        # The operator-facing path is `--no-auth-cross-quota-group` ->
        # auth_cross_quota_group=False; it must reach
        # PoolDispatchConfig.cross_quota_group, which the slot
        # coordinator reads at fallback time.
        template = _build_pool_dispatch_template(
            **common_kwargs,
            auth_cross_quota_group=False,
        )
        assert template is not None
        assert template.cross_quota_group is False

    def test_no_auth_account_returns_none_regardless_of_flag(self, common_kwargs):
        # Without --auth-account the function short-circuits to None
        # (legacy single-credential path). cross_quota_group is moot.
        common_kwargs["auth_account"] = None
        template = _build_pool_dispatch_template(
            **common_kwargs,
            auth_cross_quota_group=False,
        )
        assert template is None

    def test_include_exclude_mutex_check_still_fires(self, common_kwargs):
        # Mutual-exclusion check predates the cross-quota addition and
        # must still fire — defensive regression check.
        common_kwargs["auth_exclude_labels"] = ["laptop"]
        with pytest.raises(CLIError, match="mutually exclusive"):
            _build_pool_dispatch_template(
                **common_kwargs,
                auth_cross_quota_group=True,
            )


def test_worker_adapter_mismatch_records_durable_skip(tmp_path, caplog: pytest.LogCaptureFixture):
    template = MagicMock(adapter="claude-code-cli")
    events_path = tmp_path / "events.jsonl"
    out = MagicMock()

    with caplog.at_level(logging.WARNING, logger="metaproc.commands.run_parallel"):
        filtered = _filter_pool_dispatch_for_adapter(
            template,
            adapter_type="pi-cli",
            step_id="mine",
            events_path=events_path,
            out=out,
        )

    assert filtered is None
    message = (
        "Step 'mine' uses adapter 'pi-cli', but the credential pool is configured for "
        "'claude-code-cli'; the pool is not applied and the step uses its ambient "
        "adapter authentication."
    )
    out.warning.assert_called_once_with(message)
    assert message in caplog.messages
    record = json.loads(events_path.read_text())
    assert record["event"] == "auth_skipped"
    assert record["pool_enabled"] is False
    assert record["step_id"] == "mine"
    assert record["step_adapter"] == "pi-cli"
    assert record["configured_adapter"] == "claude-code-cli"
