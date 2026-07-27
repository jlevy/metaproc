"""Vehicle B safe-mode coverage for the slot coordinator.

Phase 5 of the Vehicle A pool redesign (epic the fix, bead
the credential-pool compatibility path).

V-B safe mode introduces a per-label mkdir-lock acquired in
``acquire_slot`` and released in ``teardown``. Two parallel V-B
attempts on the same label serialize through the lock so the
refresh-window race that produces snapshot-staleness is eliminated.
V-A leases bypass the lock entirely (no refresh writeback path).

Coverage:
- V-B lease acquires the lock dir; V-A lease doesn't.
- teardown releases the lock so the next V-B acquire on the same
  label proceeds.
- A second V-B acquire while the first lease is still held times out
  with a clear MkdirLockTimeoutError.
- Materialize-failure releases the lock so the label isn't stuck
  blocked on a half-bootstrapped lease.
- Different V-B labels do not block each other (lock is per-label).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from metaproc.adapters.base import AuthFailureClassification
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    PoolEntry,
    SelectionPolicy,
    SelectionStrategy,
    Vehicle,
    fingerprint_blob,
)
from metaproc.dispatch.slot_coordinator import (
    SlotCoordinator,
    vehicle_b_label_lock_path,
)
from metaproc.io.mkdir_lock import MkdirLockTimeoutError


@dataclass
class _InMemoryPool:
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)
    counter: int = 0

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        key = (adapter, label)
        if key not in self.entries:
            msg = f"no pool entry for {adapter}/{label}"
            raise KeyError(msg)
        return self.entries[key]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [
            entry for (a, _label), entry in self.entries.items() if adapter is None or a == adapter
        ]

    def upsert_entry(self, adapter, label, *, blob, state, expected_etag=None):
        key = (adapter, label)
        existing = self.entries.get(key)
        if expected_etag is not None and existing is not None and existing.etag != expected_etag:
            raise ConcurrentModificationError(f"stale {expected_etag} != {existing.etag}")
        self.counter += 1
        new_blob = blob if blob is not None else (existing.blob if existing else "")
        new_state = (
            state
            if state is not None
            else (existing.state if existing else EntryState(status="active", fp=""))
        )
        new_etag = f"e-{self.counter}"
        self.entries[key] = PoolEntry(
            adapter=adapter, label=label, blob=new_blob, state=new_state, etag=new_etag
        )
        return new_etag

    def delete_entry(self, adapter, label):
        self.entries.pop((adapter, label), None)


class _Adapter:
    adapter_type = "claude-code-cli"
    short_name = "claude-cli"
    default_model = None
    slot_credential_filename = ".credentials.json"
    compatible_fallback_adapters: list[str] = []  # noqa: RUF012

    def build_command(self, *args: Any, **kwargs: Any) -> list[str]:
        return ["claude"]

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

    def credential_scope_env(
        self, slot_dir: Path, *, vehicle: object = None, blob: str = ""
    ) -> dict[str, str]:
        scope = {"CLAUDE_CONFIG_DIR": str(slot_dir)}
        if vehicle is Vehicle.OAUTH_TOKEN:
            scope["CLAUDE_CODE_OAUTH_TOKEN"] = blob
        return scope

    def credential_scrub_env(self, *, vehicle: object = None) -> dict[str, str]:
        del vehicle
        return {"ANTHROPIC_AUTH_TOKEN": ""}

    def materialize_credential(self, slot_dir: Path, blob: str, *, vehicle: object = None) -> None:
        slot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if vehicle is Vehicle.OAUTH_TOKEN:
            return
        (slot_dir / self.slot_credential_filename).write_text(blob)
        (slot_dir / self.slot_credential_filename).chmod(0o600)

    def capture_credential(self) -> str:
        return '{"claudeAiOauth":{}}'

    def classify_failure(self, *_a: Any, **_kw: Any) -> AuthFailureClassification:
        return AuthFailureClassification(status="unknown")

    def flush_refreshed_credential(self, _slot_dir: Path) -> str | None:
        return None

    def query_quota_usage(self, _slot_dir: Path) -> Any:
        return None

    def query_live_quota(self, _slot_dir: Path) -> Any:
        return None

    def debug_capture_args(self, _slot_dir: Path) -> list[str]:
        return []

    def diagnostic_filenames(self) -> tuple[str, ...]:
        return ()

    def setup_token_command(self) -> list[str] | None:
        return None


@pytest.fixture
def coord(tmp_path: Path) -> tuple[SlotCoordinator, _InMemoryPool, Path]:
    pool = _InMemoryPool()
    pool.upsert_entry(
        "claude-code-cli",
        "alt1",
        blob="vb-blob",
        state=EntryState(
            status="active",
            fp=fingerprint_blob("vb-blob"),
            vehicle=Vehicle.LOGIN_CREDENTIALS,
        ),
    )
    pool.upsert_entry(
        "claude-code-cli",
        "alt2",
        blob="vb-blob-2",
        state=EntryState(
            status="active",
            fp=fingerprint_blob("vb-blob-2"),
            vehicle=Vehicle.LOGIN_CREDENTIALS,
        ),
    )
    pool.upsert_entry(
        "claude-code-cli",
        "va",
        blob="va-bearer-token",
        state=EntryState(
            status="active",
            fp=fingerprint_blob("va-bearer-token"),
            vehicle=Vehicle.OAUTH_TOKEN,
        ),
    )
    coord = SlotCoordinator(pool, adapter_registry={"claude-code-cli": _Adapter()})
    return coord, pool, tmp_path


class TestVehicleBLockAcquired:
    def test_vehicle_b_lease_creates_lock_dir(
        self, coord: tuple[SlotCoordinator, _InMemoryPool, Path]
    ) -> None:
        # On a V-B acquire, the per-label lock dir must exist on disk
        # (mkdir_lock represents "held" by directory presence).
        c, _pool, runs_dir = coord
        lease = c.acquire_slot(
            "claude-code-cli",
            runs_dir=runs_dir,
            run_id="r",
            step="s",
            item="i",
            attempt=0,
            strategy=__import__(
                "metaproc.dispatch.credential_pool", fromlist=["SelectionStrategy"]
            ).SelectionStrategy(
                __import__(
                    "metaproc.dispatch.credential_pool", fromlist=["SelectionPolicy"]
                ).SelectionPolicy.PRIORITY_ORDER,
                ("alt1",),
            ),
        )
        assert lease is not None
        assert lease.label_lock_path is not None
        assert lease.label_lock_path == vehicle_b_label_lock_path("claude-code-cli", "alt1")
        assert lease.label_lock_path.is_dir()
        c.teardown(lease, failure=None)

    def test_vehicle_a_lease_skips_lock(
        self, coord: tuple[SlotCoordinator, _InMemoryPool, Path]
    ) -> None:
        # V-A leases have no refresh writeback to protect; no lock.
        c, _pool, runs_dir = coord

        lease = c.acquire_slot(
            "claude-code-cli",
            runs_dir=runs_dir,
            run_id="r",
            step="s",
            item="i",
            attempt=0,
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("va",)),
        )
        assert lease is not None
        assert lease.label_lock_path is None
        # Lock dir must not have been created speculatively.
        assert not vehicle_b_label_lock_path("claude-code-cli", "va").exists()
        c.teardown(lease, failure=None)


class TestVehicleBLockReleased:
    def test_teardown_releases_lock(
        self, coord: tuple[SlotCoordinator, _InMemoryPool, Path]
    ) -> None:

        c, _pool, runs_dir = coord
        strategy = SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1",))
        lease = c.acquire_slot(
            "claude-code-cli",
            runs_dir=runs_dir,
            run_id="r",
            step="s",
            item="i",
            attempt=0,
            strategy=strategy,
        )
        assert lease is not None
        assert lease.label_lock_path is not None
        assert lease.label_lock_path.is_dir()
        c.teardown(lease, failure=None)
        # Lock dir gone — the next V-B acquire on this label proceeds.
        assert not lease.label_lock_path.exists()

    def test_second_acquire_after_teardown_succeeds(
        self, coord: tuple[SlotCoordinator, _InMemoryPool, Path]
    ) -> None:

        c, _pool, runs_dir = coord
        strategy = SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1",))
        l1 = c.acquire_slot(
            "claude-code-cli",
            runs_dir=runs_dir,
            run_id="r",
            step="s",
            item="i1",
            attempt=0,
            strategy=strategy,
        )
        assert l1 is not None
        c.teardown(l1, failure=None)
        l2 = c.acquire_slot(
            "claude-code-cli",
            runs_dir=runs_dir,
            run_id="r",
            step="s",
            item="i2",
            attempt=0,
            strategy=strategy,
        )
        assert l2 is not None
        c.teardown(l2, failure=None)


class TestVehicleBLockBlocksParallel:
    def test_held_lock_blocks_second_acquire(
        self, coord: tuple[SlotCoordinator, _InMemoryPool, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two items on the same V-B label, first lease still in flight:
        # second acquire times out with a MkdirLockTimeoutError.
        monkeypatch.setenv("METAPROC_AUTH_POOL_LOCK_TIMEOUT_S", "0")

        c, _pool, runs_dir = coord
        strategy = SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1",))
        l1 = c.acquire_slot(
            "claude-code-cli",
            runs_dir=runs_dir,
            run_id="r",
            step="s",
            item="i1",
            attempt=0,
            strategy=strategy,
        )
        assert l1 is not None
        try:
            with pytest.raises(MkdirLockTimeoutError, match="V-B safe-mode"):
                c.acquire_slot(
                    "claude-code-cli",
                    runs_dir=runs_dir,
                    run_id="r",
                    step="s",
                    item="i2",
                    attempt=0,
                    strategy=strategy,
                )
        finally:
            c.teardown(l1, failure=None)


class TestVehicleBLockPerLabel:
    def test_different_labels_do_not_block(
        self, coord: tuple[SlotCoordinator, _InMemoryPool, Path]
    ) -> None:

        c, _pool, runs_dir = coord
        l1 = c.acquire_slot(
            "claude-code-cli",
            runs_dir=runs_dir,
            run_id="r",
            step="s",
            item="i1",
            attempt=0,
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1",)),
        )
        l2 = c.acquire_slot(
            "claude-code-cli",
            runs_dir=runs_dir,
            run_id="r",
            step="s",
            item="i2",
            attempt=0,
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt2",)),
        )
        assert l1 is not None
        assert l2 is not None
        assert l1.label == "alt1"
        assert l2.label == "alt2"
        c.teardown(l1, failure=None)
        c.teardown(l2, failure=None)


class TestVehicleBLockReleasedOnMaterializeFailure:
    def test_materialize_raises_releases_lock(
        self, coord: tuple[SlotCoordinator, _InMemoryPool, Path]
    ) -> None:

        c, _pool, runs_dir = coord

        class _ExplodingAdapter(_Adapter):
            def materialize_credential(self, *_args: Any, **_kw: Any) -> None:
                raise RuntimeError("simulated bootstrap explosion")

        c._registry = {"claude-code-cli": _ExplodingAdapter()}  # type: ignore[attr-defined]

        with pytest.raises(RuntimeError, match="simulated bootstrap"):
            c.acquire_slot(
                "claude-code-cli",
                runs_dir=runs_dir,
                run_id="r",
                step="s",
                item="i1",
                attempt=0,
                strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1",)),
            )
        # Lock dir must be gone — next acquire isn't blocked.
        assert not vehicle_b_label_lock_path("claude-code-cli", "alt1").exists()
