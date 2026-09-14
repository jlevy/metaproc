"""Phase 2 integration tests for the auth credential pool (P2.7).

Exercises the full lifecycle — select → lease → materialize →
merge env → teardown — through the pool_dispatch + slot_coordinator
layers without starting actual subprocesses. Covers:

(a) Serial fallback: two labels, first goes cooling, retry sees fallback
(b) Parallel saturation: three slots leased concurrently by three items
(c) Cross-provider fallback: Claude cooling → Codex picked up
(d) Policy NONE: single cooling label → no retry
(e) Credential scrub invariant in merged env
(f) Non-auth-capable adapter doesn't break the dispatch
"""

from __future__ import annotations

import stat
from dataclasses import dataclass, field

import pytest

import metaproc.dispatch.pool_dispatch as pd
from metaproc.adapters.base import AuthFailureClassification
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    FallbackPolicy,
    PoolEntry,
    fingerprint_blob,
)
from metaproc.dispatch.pool_dispatch import (
    PoolAuthOverrideError,
    PoolDispatchConfig,
    PoolSlotUnavailableError,
    acquire_slot,
    classify_failure_for_slot,
    compose_slot_env,
)
from metaproc.dispatch.slot_coordinator import (
    SLOT_ACTIVE_ENV_VAR,
    SlotCoordinator,
)

# ── Fakes ────────────────────────────────────────────────────────


@dataclass
class _InMemoryPool:
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)
    counter: int = 0

    def _next_etag(self) -> str:
        self.counter += 1
        return f"et-{self.counter}"

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        key = (adapter, label)
        if key not in self.entries:
            raise KeyError(f"missing {adapter}/{label}")
        return self.entries[key]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [e for (a, _l), e in self.entries.items() if adapter is None or a == adapter]

    def upsert_entry(self, adapter, label, *, blob, state, expected_etag=None):
        key = (adapter, label)
        existing = self.entries.get(key)
        if expected_etag is not None and existing is not None and existing.etag != expected_etag:
            raise ConcurrentModificationError(f"stale {expected_etag} != {existing.etag}")
        new_blob = blob if blob is not None else (existing.blob if existing else "")
        new_state = (
            state
            if state is not None
            else (existing.state if existing else EntryState(status="active", fp=""))
        )
        new_etag = self._next_etag()
        self.entries[key] = PoolEntry(
            adapter=adapter, label=label, blob=new_blob, state=new_state, etag=new_etag
        )
        return new_etag

    def delete_entry(self, adapter, label):
        self.entries.pop((adapter, label), None)

    def seed(self, adapter, label, *, state, blob):
        self.upsert_entry(adapter, label, blob=blob, state=state)


class _StubClaude:
    adapter_type = "claude-code-cli"
    short_name = "claude-cli"
    default_model = None
    slot_credential_filename = ".credentials.json"
    compatible_fallback_adapters: list[str] = ["codex-cli"]  # noqa: RUF012 — cross-provider opt-in for this test

    def build_command(self, *a, **k) -> list[str]:  # noqa: ARG002
        return ["claude"]

    def prepare_env(self, env, _cfg):
        return env

    def working_directory(self, _cfg):
        return None

    def parse_result_event(self, _line):
        return None

    def check_auth(self):
        return None

    def auth_info(self):
        return ""

    def validate_config(self, _cfg):
        return []

    def bootstrap(self, _home):
        pass

    def credential_scope_env(self, slot_dir, *, vehicle=None, blob=""):
        # Stub mirrors ClaudeCodeCliAdapter's vehicle-aware Phase 2/3
        # signature so the SlotCoordinator's vehicle thread-through path
        # exercises the same call shape in tests.
        del vehicle, blob  # Stub doesn't branch on vehicle today.
        return {"CLAUDE_CONFIG_DIR": str(slot_dir)}

    def credential_scrub_env(self, *, vehicle=None):
        del vehicle  # Stub doesn't branch on vehicle today.
        return {
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CODE_APIKEY_HELPER": "",
        }

    def materialize_credential(self, slot_dir, blob, *, vehicle=None):
        del vehicle  # Stub doesn't branch on vehicle today.
        slot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        (slot_dir / ".credentials.json").write_text(blob)
        (slot_dir / ".credentials.json").chmod(0o600)

    def capture_credential(self):
        return '{"claudeAiOauth":{}}'

    def classify_failure(self, _exc, stderr, _log):
        if "too_many_requests" in stderr:
            return AuthFailureClassification(
                status="cooling", cooling_until_ts=9999, reason="rate-limit"
            )
        if "invalid_grant" in stderr:
            return AuthFailureClassification(status="expired", reason="invalid_grant")
        return AuthFailureClassification(status="unknown")

    def flush_refreshed_credential(self, _slot_dir):
        return None

    def query_quota_usage(self, _slot_dir):
        return None

    def query_live_quota(self, _slot_dir):
        return None

    def debug_capture_args(self, slot_dir):
        return ["-d", "api", "--debug-file", str(slot_dir / "claude-code-debug.log")]

    def diagnostic_filenames(self):
        return ("claude-code-debug.log",)

    def setup_token_command(self):
        return None


class _StubCodex(_StubClaude):
    """Codex stand-in for cross-provider fallback tests."""

    adapter_type = "codex-cli"
    short_name = "codex-cli"
    slot_credential_filename = ".codex/auth.json"
    compatible_fallback_adapters: list[str] = []  # noqa: RUF012

    def credential_scope_env(self, slot_dir, *, vehicle=None, blob=""):
        del vehicle, blob
        return {"CODEX_HOME": str(slot_dir / ".codex")}

    def credential_scrub_env(self, *, vehicle=None):
        del vehicle
        return {"CODEX_CREDS_JSON": ""}

    def materialize_credential(self, slot_dir, blob, *, vehicle=None):
        del vehicle
        codex_dir = slot_dir / ".codex"
        codex_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        (codex_dir / "auth.json").write_text(blob)
        (codex_dir / "auth.json").chmod(0o600)


@pytest.fixture
def two_claude_labels(tmp_path):
    pool = _InMemoryPool()
    pool.seed(
        "claude-code-cli",
        "laptop",
        state=EntryState(status="active", fp=fingerprint_blob("laptop-blob")),
        blob="laptop-blob",
    )
    pool.seed(
        "claude-code-cli",
        "home",
        state=EntryState(status="active", fp=fingerprint_blob("home-blob")),
        blob="home-blob",
    )
    claude = _StubClaude()
    coord = SlotCoordinator(pool, adapter_registry={"claude-code-cli": claude})
    return pool, claude, coord, tmp_path


# ── (a) Serial fallback ────────────────────────────────────────


class TestSerialFallback:
    def test_retry_after_cooling_lands_on_second_label(self, two_claude_labels, monkeypatch):

        _pool, claude, coord, tmp_path = two_claude_labels
        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: claude if name == "claude-code-cli" else None,
        )

        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            fallback_policy=FallbackPolicy.SAME_PROVIDER,
        )

        # Attempt 0: picks one of the two labels via round-robin.
        lease1 = acquire_slot(config, item="AAPL", attempt=0)
        assert lease1.label in ("laptop", "home")

        # Simulate the attempt failing with a rate-limit — teardown
        # would mark that label cooling and accumulate into
        # shared["pool_exclude"]. Mimic that here:
        classification = classify_failure_for_slot(lease1, error_str="HTTP 429 too_many_requests")
        assert classification.status == "cooling"
        coord.teardown(lease1, failure=classification)

        # Attempt 1: with exclude = (lease1.label,), picks the OTHER label.
        lease2 = acquire_slot(
            config,
            item="AAPL",
            attempt=1,
            item_exclude=((lease1.adapter, lease1.label),),
        )
        assert lease2.label != lease1.label
        assert lease2.adapter == "claude-code-cli"


# ── (b) Parallel saturation ────────────────────────────────────


class TestParallelSaturation:
    def test_three_items_lease_distinct_labels(self, tmp_path, monkeypatch):

        pool = _InMemoryPool()
        for label in ("laptop", "home", "work"):
            pool.seed(
                "claude-code-cli",
                label,
                state=EntryState(status="active", fp=fingerprint_blob(f"{label}-blob")),
                blob=f"{label}-blob",
            )
        claude = _StubClaude()
        coord = SlotCoordinator(pool, adapter_registry={"claude-code-cli": claude})
        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: claude if name == "claude-code-cli" else None,
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            fallback_policy=FallbackPolicy.SAME_PROVIDER,
        )
        # Three concurrent items — each appends the previously-leased
        # label to exclude so the coordinator walks forward through
        # the pool. (Production's coordinator runs concurrently; this
        # test exercises the exclude-accumulation invariant.)
        leases = []
        exclude: list[tuple[str, str]] = []
        for item in ("AAPL", "MSFT", "GOOGL"):
            lease = acquire_slot(
                config,
                item=item,
                attempt=0 if not exclude else 1,  # Force fallback path.
                item_exclude=tuple(exclude),
            )
            leases.append(lease)
            exclude.append((lease.adapter, lease.label))
        labels = {lease.label for lease in leases}
        assert labels == {"laptop", "home", "work"}
        # Each lease has a distinct slot_dir.
        slot_dirs = {lease.slot_dir for lease in leases}
        assert len(slot_dirs) == 3


# ── (c) Cross-provider fallback ────────────────────────────────


class TestCrossProviderFallback:
    def test_claude_cooling_falls_over_to_codex_when_both_policy(self, tmp_path, monkeypatch):

        pool = _InMemoryPool()
        # Cool the Claude label from the start.
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(
                status="cooling",
                fp=fingerprint_blob("laptop-blob"),
                cooling_until_ts=9999999999,
            ),
            blob="laptop-blob",
        )
        pool.seed(
            "codex-cli",
            "work",
            state=EntryState(status="active", fp=fingerprint_blob("codex-blob")),
            blob="codex-blob",
        )
        claude = _StubClaude()
        codex = _StubCodex()
        coord = SlotCoordinator(
            pool,
            adapter_registry={"claude-code-cli": claude, "codex-cli": codex},
        )
        # classify_failure_for_slot consults get_auth_capable for the
        # LEASED adapter — which after cross-provider walk will be
        # codex-cli. Map both.
        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: {"claude-code-cli": claude, "codex-cli": codex}.get(name),
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            fallback_policy=FallbackPolicy.BOTH,
        )
        # Claude label ineligible (cooling) and excluded → walk to
        # cross-provider landing on codex-cli.
        lease = acquire_slot(
            config,
            item="AAPL",
            attempt=1,
            item_exclude=(("claude-code-cli", "laptop"),),
        )
        assert lease.adapter == "codex-cli"
        # Scope env stages CODEX_HOME under the slot, not CLAUDE_CONFIG_DIR.
        assert "CODEX_HOME" in lease.scope_env
        assert "CLAUDE_CONFIG_DIR" not in lease.scope_env


# ── (d) Policy NONE ─────────────────────────────────────────────


class TestPolicyNoneFailsFast:
    def test_single_label_cooling_under_none_policy_raises(self, tmp_path, monkeypatch):

        pool = _InMemoryPool()
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(
                status="cooling",
                fp=fingerprint_blob("x"),
                cooling_until_ts=9999999999,
            ),
            blob="x",
        )
        claude = _StubClaude()
        coord = SlotCoordinator(pool, adapter_registry={"claude-code-cli": claude})
        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: claude if name == "claude-code-cli" else None,
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            fallback_policy=FallbackPolicy.NONE,
        )
        with pytest.raises(PoolSlotUnavailableError):
            acquire_slot(config, item="AAPL", attempt=0)


# ── (e) Credential scrubbing invariant ─────────────────────────


class TestCredentialScrubbingInvariant:
    def test_scrubbed_keys_absent_from_final_subprocess_env(self, two_claude_labels, monkeypatch):

        _pool, claude, coord, tmp_path = two_claude_labels
        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: claude if name == "claude-code-cli" else None,
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            fallback_policy=FallbackPolicy.NONE,
        )
        lease = acquire_slot(config, item="AAPL", attempt=0)

        # Parent env carries EVERY higher-precedence Claude auth var.
        parent = {
            "PATH": "/bin",
            "ANTHROPIC_AUTH_TOKEN": "would-outvote-slot",
            "CLAUDE_CODE_OAUTH_TOKEN": "would-outvote-slot",
            "CLAUDE_CODE_APIKEY_HELPER": "would-outvote-slot",
        }
        final_env = compose_slot_env(parent, lease=lease)
        assert "ANTHROPIC_AUTH_TOKEN" not in final_env
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in final_env
        assert "CLAUDE_CODE_APIKEY_HELPER" not in final_env
        assert final_env["CLAUDE_CONFIG_DIR"] == str(lease.slot_dir)
        assert final_env[SLOT_ACTIVE_ENV_VAR] == "1"
        assert final_env["PATH"] == "/bin"

    def test_anthropic_api_key_refuses_pool_slot_env(self, two_claude_labels, monkeypatch, caplog):

        _pool, claude, coord, tmp_path = two_claude_labels
        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: claude if name == "claude-code-cli" else None,
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
        )
        lease = acquire_slot(config, item="AAPL", attempt=0)
        parent = {"ANTHROPIC_API_KEY": "sk-live"}
        with caplog.at_level("WARNING"):
            with pytest.raises(PoolAuthOverrideError, match="ANTHROPIC_API_KEY"):
                compose_slot_env(parent, lease=lease, refuse_on_keys=("ANTHROPIC_API_KEY",))
        assert any("ANTHROPIC_API_KEY" in r.message for r in caplog.records)


# ── (f) Non-auth-capable adapter no-ops cleanly ────────────────


class TestNonAuthCapableNoOp:
    def test_unregistered_adapter_raises_unavailable_not_attribute_error(self, tmp_path):
        pool = _InMemoryPool()
        # Simulate a pool entry with an adapter type the registry
        # doesn't know about (e.g. Pi or a hypothetical adapter).
        pool.seed(
            "pi-cli",
            "laptop",
            state=EntryState(status="active", fp="x"),
            blob="x",
        )
        # Coordinator created without that adapter in its registry.
        coord = SlotCoordinator(pool, adapter_registry={})
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="pi-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
        )
        # Pool path cleanly surfaces "no eligible slot" rather than
        # AttributeError on a missing subprotocol method.
        with pytest.raises(PoolSlotUnavailableError):
            acquire_slot(config, item="AAPL", attempt=0)


# ── (g) Coordinator lifecycle end-to-end ───────────────────────


class TestFullLifecycle:
    def test_successful_teardown_marks_ok_releases_lease_and_cleans_slot(
        self, two_claude_labels, monkeypatch
    ):

        pool, claude, coord, tmp_path = two_claude_labels
        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: claude if name == "claude-code-cli" else None,
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
        )
        lease = acquire_slot(config, item="AAPL", attempt=0)
        # Check the slot dir materialized with the right perms.
        creds_file = lease.slot_dir / ".credentials.json"
        assert creds_file.exists()
        assert stat.S_IMODE(creds_file.stat().st_mode) == 0o600
        # Successful teardown.
        coord.teardown(lease, failure=None)
        # Pool entry is active after successful teardown.
        entry = pool.get_entry(lease.adapter, lease.label)
        assert entry.state.status == "active"
        # Slot dir cleaned.
        assert not lease.slot_dir.exists()

    def test_failed_teardown_marks_cooling_and_releases(self, two_claude_labels, monkeypatch):

        pool, claude, coord, tmp_path = two_claude_labels
        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: claude if name == "claude-code-cli" else None,
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
        )
        lease = acquire_slot(config, item="AAPL", attempt=0)
        classification = classify_failure_for_slot(lease, error_str="HTTP 429 too_many_requests")
        coord.teardown(lease, failure=classification)
        entry = pool.get_entry(lease.adapter, lease.label)
        assert entry.state.status == "cooling"
        assert entry.state.cooling_until_ts == 9999
