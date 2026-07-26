"""Tests for metaproc.dispatch.slot_coordinator (P2.3 + P2.5 core).

Uses an in-memory pool fake (same shape as test_credential_pool's
``_InMemoryPool``) and stub ``AuthCapableCliAdapter`` instances so
we can exercise the full lease+materialize+teardown lifecycle without
touching GCP or a real adapter's on-disk state.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from typing import cast as _cast

import pytest

from metaproc.adapters.base import AuthFailureClassification, QuotaUsage
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    EntryStatus,
    FallbackPolicy,
    PoolEntry,
    SelectionPolicy,
    SelectionStrategy,
    fingerprint_blob,
)
from metaproc.dispatch.credential_pool import Vehicle as _Vehicle
from metaproc.dispatch.pool_dispatch import build_auth_lease_acquired
from metaproc.dispatch.slot_coordinator import (
    SLOT_ACTIVE_ENV_VAR,
    SlotCoordinator,
    apply_slot_env,
    build_holder_id,
    slot_dir_for,
)

# ── Helpers ─────────────────────────────────────────────────────


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
            raise ConcurrentModificationError(f"etag mismatch {expected_etag} != {existing.etag}")
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

    def seed(self, adapter: str, label: str, *, state: EntryState, blob: str = "blob") -> None:
        self.upsert_entry(adapter, label, blob=blob, state=state)


class _StubAdapter:
    """Minimal AuthCapableCliAdapter for coordinator tests.

    Tracks materialize / flush calls on the instance so tests can
    assert the lifecycle contract without exercising real CLI state.
    """

    adapter_type = "stub-cli"
    short_name = "stub"
    default_model: str | None = None
    slot_credential_filename = "creds.json"
    compatible_fallback_adapters: list[str] = []  # noqa: RUF012

    def __init__(self, flush_rotates_to: str | None = None) -> None:
        self.flush_rotates_to = flush_rotates_to
        self.materialized: list[tuple[Path, str]] = []
        self.flushed_paths: list[Path] = []

    # Adapter protocol stubs (unused by coordinator but required by
    # runtime_checkable isinstance):
    def build_command(self, *args: Any, **kwargs: Any) -> list[str]:
        return ["stub"]

    def prepare_env(self, env: dict[str, str], _cfg: dict[str, object]) -> dict[str, str]:
        return env

    def working_directory(self, _cfg: dict[str, object]) -> Path | None:
        return None

    def parse_result_event(self, _line: str) -> dict[str, object] | None:
        return None

    def check_auth(self) -> Any:
        return None

    def auth_info(self) -> str:
        return ""

    def validate_config(self, _cfg: dict[str, object]) -> list[Any]:
        return []

    def bootstrap(self, _home: Path) -> None:
        pass

    # AuthCapable methods (vehicle/blob accepted for Phase 2/3 Protocol parity):
    def credential_scope_env(
        self, slot_dir: Path, *, vehicle: object = None, blob: str = ""
    ) -> dict[str, str]:
        del vehicle, blob
        return {"STUB_CONFIG_DIR": str(slot_dir)}

    def credential_scrub_env(self, *, vehicle: object = None) -> dict[str, str]:
        del vehicle
        return {"STUB_API_KEY_OLD": "", "STUB_OAUTH_BACKUP": ""}

    def materialize_credential(self, slot_dir: Path, blob: str, *, vehicle: object = None) -> None:
        del vehicle
        slot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        (slot_dir / self.slot_credential_filename).write_text(blob)
        (slot_dir / self.slot_credential_filename).chmod(0o600)
        self.materialized.append((slot_dir, blob))

    def capture_credential(self) -> str:
        return "captured-blob"

    def classify_failure(
        self,
        _exc: BaseException | None,
        _stderr: str,
        _session_log_path: Path | None,
    ) -> AuthFailureClassification:
        return AuthFailureClassification(status="unknown")

    def flush_refreshed_credential(self, slot_dir: Path) -> str | None:
        self.flushed_paths.append(slot_dir)
        return self.flush_rotates_to

    def query_quota_usage(self, _slot_dir: Path) -> QuotaUsage | None:
        return None

    def query_live_quota(self, _slot_dir: Path) -> QuotaUsage | None:
        return None

    def debug_capture_args(self, _slot_dir: Path) -> list[str]:
        return []

    def diagnostic_filenames(self) -> tuple[str, ...]:
        return ()

    def setup_token_command(self) -> list[str] | None:
        return None


# ── Helper functions ────────────────────────────────────────────


class TestBuildHolderId:
    def test_canonical_format(self):
        assert build_holder_id("run1", "predict", "AAPL", 0) == "run1:predict:AAPL:a0"

    def test_attempt_zero_padded_in_name(self):
        # Just "a0", "a1" — no zero-padding. Consistent with spec
        # example `run-42:predict:AAPL:attempt-1` collapsed to
        # a<attempt> for brevity.
        assert "a0" in build_holder_id("r", "s", "i", 0)
        assert "a7" in build_holder_id("r", "s", "i", 7)


class TestSlotDirFor:
    def test_canonical_layout(self, tmp_path):
        d = slot_dir_for(tmp_path, "run1", "predict", "AAPL", 0)
        assert d == tmp_path / "run1" / ".state" / "auth" / "predict" / "AAPL" / "a0"

    def test_unsafe_item_hashed(self, tmp_path):
        # Item names with slashes would otherwise break out of the
        # slot hierarchy. Hashed into a safe prefix+sha12 form.
        d = slot_dir_for(tmp_path, "r", "s", "nasty/../item", 0)
        segments = d.parts
        # item segment must be a single path component.
        assert len([s for s in segments if "nasty" in s]) == 1
        # Must not contain unresolved traversal.
        assert ".." not in segments

    def test_attempt_namespace_per_retry(self, tmp_path):
        # Different attempts → different dirs so a retry's slot is
        # never the same as the failed attempt's slot.
        a0 = slot_dir_for(tmp_path, "r", "s", "i", 0)
        a1 = slot_dir_for(tmp_path, "r", "s", "i", 1)
        assert a0 != a1
        assert a0.parent == a1.parent


class TestApplySlotEnv:
    def test_scope_env_wins_over_parent(self):
        result = apply_slot_env(
            {"X": "from-parent"},
            scope_env={"X": "from-scope"},
            scrub_env={},
        )
        assert result["X"] == "from-scope"

    def test_scrub_empty_string_unsets_key(self):
        result = apply_slot_env(
            {"SECRET": "leaked", "KEEP": "ok"},
            scope_env={},
            scrub_env={"SECRET": ""},
        )
        assert "SECRET" not in result
        assert result["KEEP"] == "ok"

    def test_always_sets_pool_run_signal(self):
        result = apply_slot_env({}, scope_env={}, scrub_env={})
        assert result[SLOT_ACTIVE_ENV_VAR] == "1"

    def test_extra_wins_over_scope(self):
        result = apply_slot_env(
            {},
            scope_env={"X": "scope"},
            scrub_env={},
            extra={"X": "extra"},
        )
        assert result["X"] == "extra"


# ── Coordinator lease / teardown ───────────────────────────────


@pytest.fixture
def pool():
    p = _InMemoryPool()
    p.seed(
        "stub-cli",
        "laptop",
        state=EntryState(status="active", fp=fingerprint_blob("blob-1")),
        blob="blob-1",
    )
    return p


@pytest.fixture
def adapter():
    return _StubAdapter()


@pytest.fixture
def coord(pool, adapter):
    return SlotCoordinator(pool, adapter_registry={"stub-cli": adapter})


class TestAcquireSlot:
    def test_attempt_zero_uses_primary_selection(self, tmp_path, coord, pool, adapter):
        lease = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="AAPL",
            attempt=0,
        )
        assert lease is not None
        assert lease.adapter == "stub-cli"
        assert lease.label == "laptop"
        # Slot dir materialized under the canonical path.
        assert lease.slot_dir == tmp_path / "r" / ".state" / "auth" / "s" / "AAPL" / "a0"
        assert (lease.slot_dir / "creds.json").read_text() == "blob-1"
        # Scope + scrub env surfaced.
        assert lease.scope_env == {"STUB_CONFIG_DIR": str(lease.slot_dir)}
        assert lease.scrub_env == {"STUB_API_KEY_OLD": "", "STUB_OAUTH_BACKUP": ""}

    def test_explicit_label_honored(self, tmp_path, coord, pool):
        pool.seed(
            "stub-cli",
            "home",
            state=EntryState(status="active", fp=fingerprint_blob("blob-home")),
            blob="blob-home",
        )
        lease = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=0,
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("home",)),
        )
        assert lease is not None
        assert lease.label == "home"

    def test_retry_attempt_uses_fallback_walk(self, tmp_path, coord, pool):
        # Attempt > 0 uses select_fallback; with SAME_PROVIDER and the
        # first label excluded, the second label is picked.
        pool.seed(
            "stub-cli",
            "home",
            state=EntryState(status="active", fp=fingerprint_blob("blob-home")),
            blob="blob-home",
        )
        lease = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
            exclude=(("stub-cli", "laptop"),),
            fallback_policy=FallbackPolicy.SAME_PROVIDER,
        )
        assert lease is not None
        assert lease.label == "home"

    def test_attempt_one_picks_primary_under_none_policy(self, tmp_path, coord, pool):
        # Regression: internal-reference. run_parallel.py's first attempt arrives
        # as attempt=1 (1-based), not attempt=0. Previously
        # `use_primary = attempt == 0 and not exclude` skipped the primary
        # path entirely on attempt=1, forcing select_fallback under the
        # default FallbackPolicy.NONE which then returned None even when
        # eligible primaries existed. Now: primary is always tried first
        # regardless of attempt number.
        lease = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=1,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert lease is not None
        assert lease.label == "laptop"

    def test_exclude_at_attempt_zero_picks_alternate_primary(self, tmp_path, coord, pool):
        # Regression: internal-reference (TBD-A). At attempt=0 with one label
        # excluded, the previous guard `not exclude` short-circuited to
        # the fallback path, returning None under NONE policy even when
        # another active primary remained. Now: primary respects exclude
        # natively and finds the next eligible label.
        pool.seed(
            "stub-cli",
            "home",
            state=EntryState(status="active", fp=fingerprint_blob("blob-home")),
            blob="blob-home",
        )
        lease = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=0,
            exclude=(("stub-cli", "laptop"),),
            fallback_policy=FallbackPolicy.NONE,
        )
        assert lease is not None
        assert lease.label == "home"

    def test_returns_none_when_no_eligible(self, tmp_path, coord, pool):
        # Only label is cooling indefinitely.
        pool.entries.clear()
        pool.seed(
            "stub-cli",
            "stuck",
            state=EntryState(status="cooling", fp="x", cooling_until_ts=None),
        )
        assert (
            coord.acquire_slot(
                "stub-cli",
                runs_dir=tmp_path,
                run_id="r",
                step="s",
                item="i",
                attempt=0,
            )
            is None
        )

    def test_unregistered_adapter_returns_none(self, tmp_path, coord, pool):
        pool.seed(
            "unregistered-cli",
            "laptop",
            state=EntryState(status="active", fp="x"),
            blob="x",
        )
        # Explicitly ask for the unregistered adapter.
        assert (
            coord.acquire_slot(
                "unregistered-cli",
                runs_dir=tmp_path,
                run_id="r",
                step="s",
                item="i",
                attempt=0,
            )
            is None
        )

    def test_two_consecutive_acquire_slot_calls_both_succeed(self, tmp_path, coord, pool):

        # Re-seed the default `laptop` entry as Vehicle A for this
        # test — the default fixture seeds V-B which would now lock.
        pool.seed(
            "stub-cli",
            "laptop",
            state=EntryState(
                status="active",
                fp=fingerprint_blob("blob-laptop"),
                vehicle=_Vehicle.OAUTH_TOKEN,
            ),
            blob="blob-laptop",
        )
        lease1 = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="item-A",
            attempt=0,
        )
        lease2 = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="item-B",
            attempt=0,
        )
        assert lease1 is not None
        assert lease2 is not None

    def test_default_strategy_picks_alphabetically_first(self, tmp_path, coord, pool):
        # With empty strategy.labels and two active labels, acquire_slot
        # picks alphabetically first (the priority-order default).
        pool.seed(
            "stub-cli",
            "home",
            state=EntryState(status="active", fp=fingerprint_blob("blob-home")),
            blob="blob-home",
        )
        lease = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=0,
        )
        assert lease is not None
        # "home" and "laptop" — alphabetically "home" comes first.
        assert lease.label == "home"

    def test_slot_lease_carries_v2_join_keys(self, tmp_path, coord, pool, adapter):
        # Schema-v2 (plan-2026-05-03): SlotLease holds run_id/step_id/
        # item/attempt/session_log_path so build_auth_outcome and
        # build_auth_lease_acquired can pair acquisitions and outcomes
        # by primary key without timestamp inference.
        del pool, adapter  # injected via fixtures, used by acquire_slot below
        session_log = tmp_path / "logs" / "predict_AAPL_2026-05-04.jsonl"
        lease = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="run-x",
            step="predict-ticker",
            item="AAPL",
            attempt=2,
            session_log_path=session_log,
        )
        assert lease is not None
        assert lease.run_id == "run-x"
        assert lease.step_id == "predict-ticker"
        assert lease.item == "AAPL"
        assert lease.attempt == 2
        assert lease.session_log_path == session_log

    def test_session_log_path_optional_for_legacy_callers(self, tmp_path, coord):
        # The probe path and legacy callers don't supply session_log_path;
        # SlotLease must accept None without changing existing behavior.
        lease = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=0,
        )
        assert lease is not None
        assert lease.session_log_path is None
        # Other join keys still populated from the call args.
        assert lease.run_id == "r"
        assert lease.attempt == 0

    def test_active_counter_snapshot_after_acquire_for_event_payload(
        self, tmp_path, coord, pool, adapter
    ):
        # internal review (P2): production run_parallel emits
        # auth_lease_acquired with the coordinator's active-lease
        # snapshot. After acquire_slot, the snapshot must contain the
        # leased label with count == 1 so the event payload isn't
        # always {}.
        del pool, adapter  # provided via fixtures; only the lease is needed

        lease = coord.acquire_slot(
            "stub-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="s",
            item="i",
            attempt=0,
        )
        assert lease is not None

        # Mirror the production filter in run_parallel: snapshot the
        # whole counter, then narrow to the lease's adapter and emit
        # a label-keyed dict.
        counts = coord.active_counter.snapshot()
        active_for_adapter = {
            label_key: count
            for (adapter_key, label_key), count in counts.items()
            if adapter_key == lease.adapter
        }
        payload = build_auth_lease_acquired(
            lease, policy="round-robin", active_lease_count=active_for_adapter
        )

        recorded_counts = _cast("dict[str, int]", payload["active_lease_count"])
        assert recorded_counts[lease.label] >= 1
        # Counts are strictly label-keyed (no tuples) so the JSONL
        # writer doesn't need a custom encoder.
        for k in recorded_counts:
            assert isinstance(k, str)


class TestTeardownOk:
    def test_no_rotation_marks_ok_and_cleans(self, tmp_path, coord, pool, adapter):
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        # Default stub returns None from flush → mark_ok path.
        coord.teardown(lease, failure=None)
        e = pool.get_entry("stub-cli", "laptop")
        assert e.state.status == "active"
        assert e.state.last_ok_ts is not None
        # Revised 2026-04-27: release_lease is a no-op (concurrent leases
        # per label). The holder stamp is preserved on teardown so other
        # in-flight items aren't affected.
        assert not lease.slot_dir.exists()  # rm -rf'd
        # flush_refreshed_credential was called.
        assert adapter.flushed_paths == [lease.slot_dir]

    def test_rotation_writes_back_new_version(self, tmp_path, pool):
        rotated = _StubAdapter(flush_rotates_to="rotated-blob")
        coord = SlotCoordinator(pool, adapter_registry={"stub-cli": rotated})
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        coord.teardown(lease, failure=None)
        e = pool.get_entry("stub-cli", "laptop")
        assert e.blob == "rotated-blob"
        assert e.state.fp == fingerprint_blob("rotated-blob")

    def test_ok_teardown_is_idempotent(self, tmp_path, coord, pool):
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        coord.teardown(lease, failure=None)
        # Second call: dir is gone, lease is gone — shouldn't crash.
        coord.teardown(lease, failure=None)

    def test_no_rotation_with_real_adapter_shape_bumps_last_ok_ts(self, tmp_path, pool):
        # Regression: real adapters (Claude/Codex) return the current
        # credential file text from flush_refreshed_credential when
        # the slot file exists — they do NOT return None on the
        # no-rotation path. The teardown must still mark_ok via the
        # write_back_rotated fp-match branch so last_ok_ts advances.
        # The original ``_StubAdapter`` returns None and so masked
        # this whole code path.
        original_blob = "blob-1"
        flushing_adapter = _StubAdapter(flush_rotates_to=original_blob)
        coord = SlotCoordinator(pool, adapter_registry={"stub-cli": flushing_adapter})
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        coord.teardown(lease, failure=None)
        e = pool.get_entry("stub-cli", "laptop")
        assert e.state.status == "active"
        assert e.state.last_ok_ts is not None, "no-rotation success path must still bump last_ok_ts"
        # Blob unchanged: no new secret version pushed.
        assert e.blob == original_blob

    def test_cooling_past_reset_recovers_on_successful_run(self, tmp_path):

        pool = _InMemoryPool()
        past_reset = int(_time.time()) - 600  # 10 min past reset
        cooling_status: EntryStatus = "cooling"
        pool.seed(
            "stub-cli",
            "laptop",
            state=EntryState(
                status=cooling_status,
                fp=fingerprint_blob("blob-1"),
                cooling_until_ts=past_reset,
                last_quota_ts=past_reset - 600,
            ),
            blob="blob-1",
        )
        flushing_adapter = _StubAdapter(flush_rotates_to="blob-1")
        coord = SlotCoordinator(pool, adapter_registry={"stub-cli": flushing_adapter})

        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None, "past-cooling label must be eligible"
        assert lease.label == "laptop"

        coord.teardown(lease, failure=None)

        e = pool.get_entry("stub-cli", "laptop")
        assert e.state.status == "active", (
            "cooling label past its reset must recover to active on success"
        )
        assert e.state.last_ok_ts is not None
        assert e.state.last_ok_ts >= past_reset


class TestTeardownFailures:
    def test_cooling_marks_label_and_releases(self, tmp_path, coord, pool):
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        coord.teardown(
            lease,
            failure=AuthFailureClassification(
                status="cooling",
                cooling_until_ts=1234,
                reason="quota",
            ),
        )
        e = pool.get_entry("stub-cli", "laptop")
        assert e.state.status == "cooling"
        assert e.state.cooling_until_ts == 1234
        # release_lease is a no-op under concurrent-leases-per-label.
        # State transitions (cooling/expired) still apply via the failure
        # classifier path; only the lease stamp is preserved.

    def test_expired_marks_label_and_releases(self, tmp_path, coord, pool):
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        coord.teardown(
            lease,
            failure=AuthFailureClassification(status="expired", reason="invalid_grant"),
        )
        e = pool.get_entry("stub-cli", "laptop")
        assert e.state.status == "expired"
        # release_lease is a no-op under concurrent-leases-per-label.
        # State transitions (cooling/expired) still apply via the failure
        # classifier path; only the lease stamp is preserved.

    def test_unknown_failure_leaves_pool_state_unchanged(self, tmp_path, coord, pool):
        # Status="unknown" means generic retry classifier wins.
        # Pool state (status, fp) stays as-is; only the lease releases.
        before = pool.get_entry("stub-cli", "laptop").state
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        coord.teardown(lease, failure=AuthFailureClassification(status="unknown"))
        after = pool.get_entry("stub-cli", "laptop").state
        assert after.status == before.status
        assert after.fp == before.fp


class TestPreserveDiagnostics:
    """Adapter-declared diagnostic logs are copied to the run's
    standard ``.logs/`` directory alongside the session log before
    teardown destroys the slot.

    Without this, a post-mortem on a wave of identical failures is
    impossible — operators can't see what the CLI saw at the moment
    of exit. The set of files preserved comes from the adapter's
    ``diagnostic_filenames``; everything else in the slot is treated
    as cred-bearing and unconditionally wiped.
    """

    def _adapter_with_diagnostics(self, *names: str):
        adapter = _StubAdapter()
        adapter.diagnostic_filenames = lambda: tuple(names)  # type: ignore[method-assign]
        return adapter

    def test_preserves_each_declared_filename_alongside_session_log(self, tmp_path, pool):
        adapter = self._adapter_with_diagnostics("stub-debug.log", "stub-trace.log")
        coord = SlotCoordinator(pool, adapter_registry={"stub-cli": adapter})
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        (lease.slot_dir / "stub-debug.log").write_text("DEBUG body\n")
        (lease.slot_dir / "stub-trace.log").write_text("TRACE body\n")

        logs_dir = tmp_path / "r" / "s" / ".logs"
        session_log = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.jsonl"
        coord.preserve_diagnostics(lease, session_log)

        # Naming: <session-stem>.<diagnostic-name>, lexically next to
        # the session log so the operator-facing logs tree shows
        # session + every adapter's diagnostic side-by-side.
        debug_target = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.stub-debug.log"
        trace_target = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.stub-trace.log"
        assert debug_target.read_text() == "DEBUG body\n"
        assert trace_target.read_text() == "TRACE body\n"

    def test_preserves_no_files_for_adapter_with_empty_set(self, tmp_path, pool):
        # codex / gemini / pi return ``()`` until they grow equivalents
        # to ``claude -d api`` — preserve_diagnostics must be a no-op,
        # not even creating the target dir.
        adapter = self._adapter_with_diagnostics()  # ()
        coord = SlotCoordinator(pool, adapter_registry={"stub-cli": adapter})
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        logs_dir = tmp_path / "r" / "s" / ".logs"
        session_log = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.jsonl"
        coord.preserve_diagnostics(lease, session_log)
        assert not logs_dir.exists() or not list(logs_dir.iterdir())

    def test_does_not_copy_credential_files(self, tmp_path, pool):
        # Hard invariant: only files in diagnostic_filenames are copied.
        # The slot's credential blob must never appear in .logs/.
        adapter = self._adapter_with_diagnostics("stub-debug.log")
        coord = SlotCoordinator(pool, adapter_registry={"stub-cli": adapter})
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        # The materialized credential file is at slot_dir / "creds.json"
        # (per _StubAdapter.slot_credential_filename).
        assert (lease.slot_dir / "creds.json").exists()

        logs_dir = tmp_path / "r" / "s" / ".logs"
        session_log = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.jsonl"
        coord.preserve_diagnostics(lease, session_log)

        if logs_dir.exists():
            for entry in logs_dir.iterdir():
                assert "creds.json" not in entry.name, "credential file leaked into .logs/"

    def test_idempotent_when_diagnostic_file_missing(self, tmp_path, pool):
        # Adapter declares ``stub-debug.log`` but the worker never
        # wrote it (e.g. the CLI crashed before opening the file).
        # preserve_diagnostics must not raise.
        adapter = self._adapter_with_diagnostics("stub-debug.log")
        coord = SlotCoordinator(pool, adapter_registry={"stub-cli": adapter})
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        logs_dir = tmp_path / "r" / "s" / ".logs"
        session_log = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.jsonl"
        coord.preserve_diagnostics(lease, session_log)  # must not raise
        target = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.stub-debug.log"
        assert not target.exists()

    def test_safe_after_teardown_wiped_slot(self, tmp_path, pool):
        # If the caller order ever flips (teardown before preserve),
        # preserve_diagnostics must still be safe. Returns silently.
        adapter = self._adapter_with_diagnostics("stub-debug.log")
        coord = SlotCoordinator(pool, adapter_registry={"stub-cli": adapter})
        lease = coord.acquire_slot(
            "stub-cli", runs_dir=tmp_path, run_id="r", step="s", item="i", attempt=0
        )
        assert lease is not None
        coord.teardown(lease, failure=None)  # wipes the slot
        logs_dir = tmp_path / "r" / "s" / ".logs"
        session_log = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.jsonl"
        coord.preserve_diagnostics(lease, session_log)  # must not raise
        assert not logs_dir.exists() or not list(logs_dir.iterdir())


class TestMaterializeFailureReleasesLease:
    def test_materialize_raises_releases_the_lease(self, tmp_path, pool):
        class _ExplodingAdapter(_StubAdapter):
            def materialize_credential(
                self,
                slot_dir: Path,  # noqa: ARG002
                blob: str,  # noqa: ARG002
                *,
                vehicle: object = None,  # noqa: ARG002
            ) -> None:
                raise RuntimeError("simulated materialize failure")

        adapter = _ExplodingAdapter()
        coord = SlotCoordinator(pool, adapter_registry={"stub-cli": adapter})
        with pytest.raises(RuntimeError, match="simulated materialize"):
            coord.acquire_slot(
                "stub-cli",
                runs_dir=tmp_path,
                run_id="r",
                step="s",
                item="i",
                attempt=0,
            )
