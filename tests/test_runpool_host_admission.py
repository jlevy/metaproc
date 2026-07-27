"""Tests for disk-backed RunPool host admission."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from metaproc.runpool.host_admission import (
    UNRECORDED_LEASE_GRACE_S,
    HostAdmissionGate,
)


def test_reclaims_stale_slot_when_recorded_processes_are_dead(tmp_path: Path) -> None:
    namespace_dir = tmp_path / "slots" / "local-agents"
    slot_dir = namespace_dir / "slot-0"
    slot_dir.mkdir(parents=True)
    _ = (slot_dir / "lease.json").write_text(
        json.dumps(
            {
                "schema": "metaproc.runpool.host-admission/v1",
                "namespace": "local-agents",
                "slot_id": 0,
                "limit": 1,
                "token": "old",
                "label": "old",
                "pool_id": "old-pool",
                "owner_pid": 999999999,
                "owner_create_time": None,
                "child_pid": None,
                "child_create_time": None,
                "acquired_at": "2026-05-17T00:00:00Z",
                "updated_at": "2026-05-17T00:00:00Z",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    old_mtime = time.time() - UNRECORDED_LEASE_GRACE_S - 1
    os.utime(slot_dir, (old_mtime, old_mtime))

    gate = HostAdmissionGate(root_dir=tmp_path / "slots", limit=1, poll_interval_s=0.01)

    async def _run():
        lease = await gate.acquire(label="new", pool_id="pool-new")
        assert lease.slot_id == 0
        assert lease.token != "old"
        gate.release(lease)

    asyncio.run(_run())
    assert not slot_dir.exists()


def test_does_not_reclaim_slot_with_live_child(tmp_path: Path) -> None:
    namespace_dir = tmp_path / "slots" / "local-agents"
    slot_dir = namespace_dir / "slot-0"
    slot_dir.mkdir(parents=True)
    _ = (slot_dir / "lease.json").write_text(
        json.dumps(
            {
                "schema": "metaproc.runpool.host-admission/v1",
                "namespace": "local-agents",
                "slot_id": 0,
                "limit": 2,
                "token": "old",
                "label": "old",
                "pool_id": "old-pool",
                "owner_pid": 999999999,
                "owner_create_time": None,
                "child_pid": os.getpid(),
                "child_create_time": None,
                "acquired_at": "2026-05-17T00:00:00Z",
                "updated_at": "2026-05-17T00:00:00Z",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    gate = HostAdmissionGate(root_dir=tmp_path / "slots", limit=2, poll_interval_s=0.01)

    async def _run():
        lease = await gate.acquire(label="new", pool_id="pool-new")
        assert lease.slot_id == 1
        gate.release(lease)

    asyncio.run(_run())
    assert slot_dir.exists()
    shutil.rmtree(slot_dir)


def test_does_not_reclaim_recent_unrecorded_lease(tmp_path: Path) -> None:
    namespace_dir = tmp_path / "slots" / "local-agents"
    slot_dir = namespace_dir / "slot-0"
    slot_dir.mkdir(parents=True)
    _ = (slot_dir / "lease.json").write_text(
        json.dumps(
            {
                "schema": "metaproc.runpool.host-admission/v1",
                "namespace": "local-agents",
                "slot_id": 0,
                "limit": 1,
                "token": "old",
                "label": "old",
                "pool_id": "old-pool",
                "owner_pid": 999999999,
                "owner_create_time": None,
                "child_pid": None,
                "child_create_time": None,
                "acquired_at": "2026-05-17T00:00:00Z",
                "updated_at": "2026-05-17T00:00:00Z",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    gate = HostAdmissionGate(root_dir=tmp_path / "slots", limit=1, poll_interval_s=0.01)

    async def _run():
        try:
            _ = await asyncio.wait_for(
                gate.acquire(label="new", pool_id="pool-new"),
                timeout=0.05,
            )
        except TimeoutError:
            return
        raise AssertionError("recent unrecorded lease should not be reclaimed")

    asyncio.run(_run())
    assert slot_dir.exists()
    shutil.rmtree(slot_dir)


def test_acquire_times_out_when_all_slots_remain_live(tmp_path: Path) -> None:
    gate = HostAdmissionGate(
        root_dir=tmp_path / "slots",
        limit=1,
        poll_interval_s=0.005,
        acquire_timeout_s=0.02,
    )

    async def _run() -> None:
        lease = await gate.acquire(label="first", pool_id="pool-first")
        with pytest.raises(TimeoutError, match="host admission slot"):
            await gate.acquire(label="second", pool_id="pool-second")
        gate.release(lease)

    asyncio.run(_run())
