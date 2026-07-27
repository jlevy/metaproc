"""Two-label end-to-end smoke for the auth credential pool — Vehicle A.

Sibling of ``test_two_label_smoke.py`` that exercises the Vehicle A
(``CLAUDE_CODE_OAUTH_TOKEN`` static-bearer) materialization path end to
end: push two fake-but-shape-valid V-A tokens into a local pool, acquire
a slot, and assert the per-lease invariants — token in scope_env, no
``.credentials.json`` written, scrub_env contents reflect the V-A
variant.

Spec: ``docs/arch/arch-metaproc-core.md``
§Phase 11 Band A.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from metaproc.dispatch.credential_pool import (
    EntryState,
    FallbackPolicy,
    LocalFilesystemBackend,
    SelectionPolicy,
    SelectionStrategy,
    Vehicle,
)
from metaproc.dispatch.slot_coordinator import SlotCoordinator

_FAKE_TOKEN_PREFIX = "sk-ant-oat01-VA-SMOKE-"


def _push_vehicle_a(
    backend: LocalFilesystemBackend,
    label: str,
    *,
    fp: str,
    tag: str,
) -> None:
    blob = _FAKE_TOKEN_PREFIX + tag
    state = EntryState(
        status="active",
        fp=fp,
        last_ok_ts=int(time.time()),
        vehicle=Vehicle.OAUTH_TOKEN,
    )
    backend.upsert_entry("claude-code-cli", label, blob=blob, state=state)


@pytest.fixture
def two_label_pool_va(tmp_path: Path) -> LocalFilesystemBackend:
    backend = LocalFilesystemBackend(path=tmp_path / "credentials.json")
    _push_vehicle_a(backend, "alt1", fp="aa11aa11aa11", tag="alt1")
    _push_vehicle_a(backend, "alt2", fp="bb22bb22bb22", tag="alt2")
    return backend


@pytest.fixture
def coord_va(two_label_pool_va: LocalFilesystemBackend) -> SlotCoordinator:
    return SlotCoordinator(two_label_pool_va)


@pytest.fixture
def lease_kwargs(tmp_path: Path) -> dict[str, Any]:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return {
        "runs_dir": runs_dir,
        "run_id": "va-smoke",
        "step": "predict-ticker",
        "item": "AAPL",
    }


class TestVehicleAPoolEligibility:
    def test_two_labels_eligible(self, two_label_pool_va: LocalFilesystemBackend) -> None:
        entries = two_label_pool_va.list_entries(adapter="claude-code-cli")
        labels = sorted(e.label for e in entries)
        assert labels == ["alt1", "alt2"]
        assert all(e.state.status == "active" for e in entries)
        assert all(e.state.vehicle == Vehicle.OAUTH_TOKEN for e in entries)


class TestVehicleASlotMaterialization:
    """Per-lease invariants for V-A: token in env, no credentials.json on disk."""

    def test_scope_env_injects_oauth_token(
        self, coord_va: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord_va.acquire_slot(adapter="claude-code-cli", attempt=0, **lease_kwargs)
        assert lease is not None
        assert "CLAUDE_CONFIG_DIR" in lease.scope_env
        # The defining V-A property: the bearer token reaches the
        # subprocess via env var, NOT via a slot file.
        assert "CLAUDE_CODE_OAUTH_TOKEN" in lease.scope_env
        assert lease.scope_env["CLAUDE_CODE_OAUTH_TOKEN"].startswith(_FAKE_TOKEN_PREFIX)
        coord_va.teardown(lease, failure=None)

    def test_scope_env_includes_va_hardening_flags(
        self, coord_va: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord_va.acquire_slot(adapter="claude-code-cli", attempt=0, **lease_kwargs)
        assert lease is not None
        # Per spec §Slot bootstrap — vehicle-aware: V-A scope env
        # carries the subprocess scrub + auto-update lockout flags so
        # the inner claude can't refresh tokens or pick up an ambient
        # CLI version mid-run.
        assert lease.scope_env.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB") == "1"
        assert lease.scope_env.get("DISABLE_UPDATES") == "1"
        coord_va.teardown(lease, failure=None)

    def test_no_credentials_json_written_to_slot(
        self, coord_va: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord_va.acquire_slot(adapter="claude-code-cli", attempt=0, **lease_kwargs)
        assert lease is not None
        config_dir = Path(lease.scope_env["CLAUDE_CONFIG_DIR"])
        cred_path = config_dir / ".credentials.json"
        # The whole point of V-A: no refresh-rotating snapshot in the
        # slot. If this file appears, V-A has silently regressed to the
        # V-B materialization path (which Anthropic precedence then
        # picks AFTER Vehicle A — a stale snapshot would still work
        # but the regression hides the bug).
        assert not cred_path.exists(), (
            "Vehicle A must NOT materialize .credentials.json in the slot — "
            "the bearer token is the credential, injected via scope_env"
        )
        coord_va.teardown(lease, failure=None)

    def test_scrub_env_strips_competing_oauth_sources(
        self, coord_va: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord_va.acquire_slot(adapter="claude-code-cli", attempt=0, **lease_kwargs)
        assert lease is not None
        scrub_keys = set(lease.scrub_env.keys())
        # V-A scrubs the higher-precedence OAuth fork (ANTHROPIC_AUTH_TOKEN
        # wins over CLAUDE_CODE_OAUTH_TOKEN per Anthropic precedence) +
        # the apiKeyHelper hook + the cloud-provider mode flags. It
        # does NOT scrub CLAUDE_CODE_OAUTH_TOKEN itself — that's the
        # leased credential.
        assert "ANTHROPIC_AUTH_TOKEN" in scrub_keys
        assert "CLAUDE_CODE_APIKEY_HELPER" in scrub_keys
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in scrub_keys, (
            "V-A scrub must not strip the very env var it is injecting"
        )
        coord_va.teardown(lease, failure=None)


class TestVehicleAFailoverWalk:
    """Two-label V-A failover: alt1 cooling → next attempt picks alt2."""

    def test_exclude_at_attempt_zero_picks_alternate(
        self, coord_va: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord_va.acquire_slot(
            adapter="claude-code-cli",
            attempt=0,
            exclude=(("claude-code-cli", "alt1"),),
            fallback_policy=FallbackPolicy.NONE,
            **lease_kwargs,
        )
        assert lease is not None
        assert lease.label == "alt2"
        # alt2's blob should be carried as the leased token.
        assert lease.scope_env["CLAUDE_CODE_OAUTH_TOKEN"].endswith("alt2")
        coord_va.teardown(lease, failure=None)

    def test_explicit_label_strategy_honored(
        self, coord_va: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord_va.acquire_slot(
            adapter="claude-code-cli",
            attempt=0,
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt2",)),
            **lease_kwargs,
        )
        assert lease is not None
        assert lease.label == "alt2"
        coord_va.teardown(lease, failure=None)


class TestVehicleASchemaPersistence:
    """Round-trip: pushing V-A entries persists vehicle correctly across read."""

    def test_vehicle_field_round_trips_through_local_backend(self, tmp_path: Path) -> None:
        backend = LocalFilesystemBackend(path=tmp_path / "creds.json")
        _push_vehicle_a(backend, "alt1", fp="cc33cc33cc33", tag="rt1")
        # Re-open from disk to force a fresh read off the schema-v2 path.
        backend2 = LocalFilesystemBackend(path=tmp_path / "creds.json")
        entry = backend2.get_entry("claude-code-cli", "alt1")
        assert entry.state.vehicle == Vehicle.OAUTH_TOKEN
        assert entry.blob.startswith(_FAKE_TOKEN_PREFIX)
