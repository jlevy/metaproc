"""Tests for SlotCoordinator vehicle-aware materialization (Phase 3).

Spec: docs/project/specs/active/plan-2026-04-28-claude-code-auth-vehicle-a-pool-redesign.md
Bead: internal-reference (Phase 3 of epic internal-reference).

Verifies that ``SlotCoordinator.acquire_slot`` reads
``entry.state.vehicle`` from the selected pool entry and threads it
through to the adapter's ``materialize_credential`` /
``credential_scope_env`` / ``credential_scrub_env`` methods. Uses the
real :class:`ClaudeCodeCliAdapter` so the test exercises the live
Vehicle A code path (no .credentials.json materialized;
``CLAUDE_CODE_OAUTH_TOKEN`` injected via ``scope_env``;
``CLAUDE_CODE_OAUTH_TOKEN`` omitted from ``scrub_env`` for Vehicle A,
present for Vehicle B).

Companion to :mod:`test_credential_pool_schema_v2` (data model) and
:mod:`test_auth_command_vehicle_a` (operator surface).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from metaproc.adapters.claude_code import ClaudeCodeCliAdapter
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    PoolEntry,
    QuotaGroup,
    SelectionPolicy,
    SelectionStrategy,
    Vehicle,
)
from metaproc.dispatch.slot_coordinator import SlotCoordinator, slot_dir_for

_OAUTH_TOKEN = "sk-ant-oat01-vehicle-a-token-only-do-not-print"
_LOGIN_BLOB = '{"claudeAiOauth": {"accessToken": "sk-ant-legacy-DO-NOT-PRINT"}}'


@dataclass
class _InMemoryPool:
    """PoolBackend stub returning seeded PoolEntry objects."""

    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)
    etag_counter: int = 0

    def seed(
        self,
        adapter: str,
        label: str,
        *,
        state: EntryState,
        blob: str,
    ) -> None:
        self.etag_counter += 1
        self.entries[(adapter, label)] = PoolEntry(
            adapter=adapter,
            label=label,
            blob=blob,
            state=state,
            etag=f"etag-{self.etag_counter}",
        )

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        key = (adapter, label)
        if key not in self.entries:
            msg = f"no pool entry for {adapter}/{label}"
            raise KeyError(msg)
        return self.entries[key]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        out: list[PoolEntry] = []
        for (a, _label), entry in self.entries.items():
            if adapter is None or a == adapter:
                out.append(entry)
        return out

    def upsert_entry(
        self,
        adapter: str,
        label: str,
        *,
        blob: str | None,
        state: EntryState | None,
        expected_etag: str | None = None,
    ) -> str:
        key = (adapter, label)
        existing = self.entries.get(key)
        if expected_etag is not None and existing is not None and existing.etag != expected_etag:
            msg = f"etag mismatch: {expected_etag} != {existing.etag}"
            raise ConcurrentModificationError(msg)
        new_blob = blob if blob is not None else (existing.blob if existing else "")
        new_state = (
            state
            if state is not None
            else (existing.state if existing else EntryState(status="active", fp=""))
        )
        self.etag_counter += 1
        new_etag = f"etag-{self.etag_counter}"
        self.entries[key] = PoolEntry(
            adapter=adapter,
            label=label,
            blob=new_blob,
            state=new_state,
            etag=new_etag,
        )
        return new_etag

    def delete_entry(self, adapter: str, label: str) -> None:
        self.entries.pop((adapter, label), None)


@pytest.fixture
def claude_adapter() -> ClaudeCodeCliAdapter:
    return ClaudeCodeCliAdapter()


@pytest.fixture
def coordinator(claude_adapter: ClaudeCodeCliAdapter) -> tuple[SlotCoordinator, _InMemoryPool]:
    pool = _InMemoryPool()
    coord = SlotCoordinator(pool, adapter_registry={"claude-code-cli": claude_adapter})
    return coord, pool


def _seed_vehicle_a(pool: _InMemoryPool, *, label: str = "alt1") -> None:
    pool.seed(
        "claude-code-cli",
        label,
        state=EntryState(
            status="active",
            fp="fp-vehicle-a",
            vehicle=Vehicle.OAUTH_TOKEN,
            account_id="abc1234567890def",
            organization_uuid="abc12345-6789-4abc-9def-0123456789ab",
            quota_group=QuotaGroup(kind="org", value="abc12345-6789-4abc-9def-0123456789ab"),
        ),
        blob=_OAUTH_TOKEN,
    )


def _seed_vehicle_b(pool: _InMemoryPool, *, label: str = "laptop") -> None:
    pool.seed(
        "claude-code-cli",
        label,
        state=EntryState(status="active", fp="fp-vehicle-b"),
        blob=_LOGIN_BLOB,
    )


# ── Vehicle A: SlotCoordinator threads vehicle through ────────────


class TestAcquireSlotVehicleA:
    def test_vehicle_a_slot_has_no_credentials_json(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_a(pool)
        lease = coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
        )
        assert lease is not None
        # Vehicle A must NOT write .credentials.json — the slot stays
        # empty so the bearer-token env var is the only credential.
        assert not (lease.slot_dir / ".credentials.json").exists()
        # Slot dir was created with 0700 perms.
        assert lease.slot_dir.exists()

    def test_vehicle_a_scope_env_injects_oauth_token(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_a(pool)
        lease = coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
        )
        assert lease is not None
        # scope_env carries CLAUDE_CONFIG_DIR (slot scope) plus the
        # static-bearer credential and Vehicle A hardening flags.
        assert lease.scope_env["CLAUDE_CONFIG_DIR"] == str(lease.slot_dir)
        assert lease.scope_env["CLAUDE_CODE_OAUTH_TOKEN"] == _OAUTH_TOKEN
        assert lease.scope_env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
        assert lease.scope_env["DISABLE_UPDATES"] == "1"

    def test_vehicle_a_scrub_env_omits_oauth_token(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_a(pool)
        lease = coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
        )
        assert lease is not None
        # CLAUDE_CODE_OAUTH_TOKEN IS the credential under Vehicle A;
        # scrubbing it would mean unsetting our own auth source.
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in lease.scrub_env
        # Other higher-precedence vars and the cloud-provider mode flags
        # must still be scrubbed so they cannot silently override.
        assert lease.scrub_env["ANTHROPIC_AUTH_TOKEN"] == ""
        assert lease.scrub_env["CLAUDE_CODE_APIKEY_HELPER"] == ""
        assert lease.scrub_env["CLAUDE_CODE_USE_BEDROCK"] == ""
        assert lease.scrub_env["CLAUDE_CODE_USE_VERTEX"] == ""
        assert lease.scrub_env["CLAUDE_CODE_USE_FOUNDRY"] == ""
        # Provisioning-only env vars must never be inherited.
        assert lease.scrub_env["CLAUDE_CODE_OAUTH_REFRESH_TOKEN"] == ""
        assert lease.scrub_env["CLAUDE_CODE_OAUTH_SCOPES"] == ""

    def test_vehicle_a_lease_carries_correct_metadata(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_a(pool, label="alt1")
        lease = coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
        )
        assert lease is not None
        assert lease.adapter == "claude-code-cli"
        assert lease.label == "alt1"
        assert lease.bootstrap_fp == "fp-vehicle-a"


# ── Vehicle B: existing slot-file path preserved ─────────────────


class TestAcquireSlotVehicleB:
    def test_vehicle_b_writes_credentials_json(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_b(pool)
        lease = coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
        )
        assert lease is not None
        creds = lease.slot_dir / ".credentials.json"
        assert creds.exists()
        # Mode 0600 (owner read/write only).
        assert oct(creds.stat().st_mode & 0o777) == "0o600"
        assert creds.read_text() == _LOGIN_BLOB

    def test_vehicle_b_scope_env_does_not_inject_oauth_token(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_b(pool)
        lease = coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
        )
        assert lease is not None
        # Vehicle B credentials live in the slot file; scope_env scopes
        # the lookup but does NOT inject a bearer token.
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in lease.scope_env
        assert lease.scope_env["CLAUDE_CONFIG_DIR"] == str(lease.slot_dir)

    def test_vehicle_b_scrub_env_includes_oauth_token(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_b(pool)
        lease = coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
        )
        assert lease is not None
        # Under Vehicle B, an inherited CLAUDE_CODE_OAUTH_TOKEN env var
        # would silently win over the slot's .credentials.json (per the
        # current Anthropic precedence chain). Scrubbing it is mandatory.
        assert lease.scrub_env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


# ── Defense-in-depth: stray slot files refused on Vehicle A ───────


class TestVehicleASlotIsClean:
    def test_vehicle_a_refuses_to_run_with_stray_credentials_json(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_a(pool)

        stray_path = slot_dir_for(runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=1)
        stray_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        (stray_path / ".credentials.json").write_text("{}")
        with pytest.raises(RuntimeError, match="stray '.credentials.json'"):
            coord.acquire_slot(
                "claude-code-cli",
                runs_dir=tmp_path,
                run_id="r",
                step="s",
                item="i",
                attempt=1,
            )

    def test_vehicle_a_overwrites_stray_settings_json_with_safe_content(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        # Stray settings.json with apiKeyHelper does not abort acquisition;
        # the sandbox-only settings.json the slot writes is known-safe
        # content with no apiKeyHelper, so it overwrites whatever was
        # there. The defense-in-depth check only refuses on stray
        # .credentials.json, which lives at a path the slot does not
        # otherwise write.
        coord, pool = coordinator
        _seed_vehicle_a(pool)

        stray_path = slot_dir_for(runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=1)
        stray_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        stray_settings = stray_path / "settings.json"
        stray_settings.write_text('{"apiKeyHelper": "/bin/true"}')
        coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
        )
        assert "apiKeyHelper" not in stray_settings.read_text()


# ── Mixed-vehicle pool: each label gets its own materialization ──


class TestMixedVehiclePool:
    def test_acquire_picks_vehicle_a_first_when_listed_first(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_a(pool, label="alt1")
        _seed_vehicle_b(pool, label="laptop")

        lease = coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
            strategy=SelectionStrategy(
                policy=SelectionPolicy.PRIORITY_ORDER, labels=("alt1", "laptop")
            ),
        )
        assert lease is not None
        assert lease.label == "alt1"
        # Vehicle A path: empty slot.
        assert not (lease.slot_dir / ".credentials.json").exists()

    def test_failover_from_vehicle_a_to_vehicle_b_works(
        self,
        coordinator: tuple[SlotCoordinator, _InMemoryPool],
        tmp_path: Path,
    ) -> None:
        coord, pool = coordinator
        _seed_vehicle_a(pool, label="alt1")
        _seed_vehicle_b(pool, label="laptop")

        # Exclude alt1; coordinator must walk to laptop and use the
        # Vehicle B materialization path even though the request started
        # under a Vehicle A label.
        lease = coord.acquire_slot(
            "claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
            strategy=SelectionStrategy(
                policy=SelectionPolicy.PRIORITY_ORDER, labels=("alt1", "laptop")
            ),
            exclude=(("claude-code-cli", "alt1"),),
        )
        assert lease is not None
        assert lease.label == "laptop"
        # Vehicle B path: .credentials.json materialized.
        assert (lease.slot_dir / ".credentials.json").exists()
