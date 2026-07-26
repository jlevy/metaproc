"""Tests for claimed-item registry helpers used by cloud worker scaling."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from metaproc.io.claimed_items import (
    CLAIM_LOCK_DIR,
    CLAIM_LOCK_STALE_S,
    ClaimLockTimeoutError,
    claim_item,
    clear_claimed_items,
    collect_claimed_items,
    read_claimed_items,
)
from metaproc.paths import step_state_dir


class TestClaimedItems:
    def test_worker_claim_roundtrip(self, tmp_path: Path) -> None:
        assert claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL") is True

        record = read_claimed_items(tmp_path, "generate-record", worker_id="0")
        assert record is not None
        assert record.worker_id == "0"
        assert record.items == ["AAPL"]

    def test_other_worker_cannot_claim_existing_item(self, tmp_path: Path) -> None:
        assert claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL") is True
        assert claim_item(tmp_path, "generate-record", worker_id="1", item="AAPL") is False
        assert claim_item(tmp_path, "generate-record", worker_id="1", item="MSFT") is True

        assert collect_claimed_items(tmp_path, "generate-record") == {"AAPL", "MSFT"}

    def test_same_worker_can_reuse_existing_claim(self, tmp_path: Path) -> None:
        assert claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL") is True
        assert claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL") is True

        record = read_claimed_items(tmp_path, "generate-record", worker_id="0")
        assert record is not None
        assert record.items == ["AAPL"]

    def test_clear_claimed_items_removes_worker_registry(self, tmp_path: Path) -> None:
        assert claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL") is True

        clear_claimed_items(tmp_path, "generate-record", worker_id="0")

        assert read_claimed_items(tmp_path, "generate-record", worker_id="0") is None


class TestStepClaimLock:
    def _lock_path(self, run_dir: Path, step_id: str) -> Path:
        # New layout: <run>/.state/steps/<step_id>/.claim.lock/
        return step_state_dir(run_dir, step_id) / CLAIM_LOCK_DIR

    def test_claim_releases_lock_directory(self, tmp_path: Path) -> None:
        assert claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL") is True
        assert not self._lock_path(tmp_path, "generate-record").exists()

    def test_stale_lock_is_reclaimed(self, tmp_path: Path) -> None:
        lock_path = self._lock_path(tmp_path, "generate-record")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.mkdir()

        stale_time = time.time() - (CLAIM_LOCK_STALE_S + 5)
        os.utime(lock_path, (stale_time, stale_time))

        assert claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL") is True

    def test_held_lock_raises_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("metaproc.io.claimed_items.CLAIM_LOCK_TIMEOUT_S", 0.1)
        monkeypatch.setattr("metaproc.io.claimed_items.CLAIM_LOCK_POLL_S", 0.01)
        lock_path = self._lock_path(tmp_path, "generate-record")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.mkdir()

        with pytest.raises(ClaimLockTimeoutError):
            claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL")
