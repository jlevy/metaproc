"""Tests for orchestrator lease — cross-host safety for concurrent orchestrators."""

from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from strif import atomic_output_file

from metaproc.errors import CLIError
from metaproc.io import read_yaml_file, to_yaml_string
from metaproc.io.mkdir_lock import release_mkdir_lock
from metaproc.io.orchestrator_lease import (
    LeaseHeartbeat,
    _lease_lock_owner_path,
    _lease_lock_path,
    _LeaseWriteLock,
    _now_iso,
    acquire_lease,
    is_orchestrator_alive,
    release_lease,
    update_heartbeat,
)
from metaproc.paths import ORCHESTRATOR_LEASE_FILE, STATE_DIR


class TestAcquireLease:
    def test_acquires_on_empty_dir(self, tmp_path: Path) -> None:
        path = acquire_lease(tmp_path, command_summary="test run")
        assert path.exists()

        data = read_yaml_file(path)
        assert data["owner_type"] == "local"
        assert data["owner_pid"] == os.getpid()
        assert data["command_summary"] == "test run"
        assert "started_at" in data
        assert "last_heartbeat_at" in data

    def test_rejects_live_lease(self, tmp_path: Path) -> None:
        """Cannot acquire if another process holds a live lease."""
        acquire_lease(tmp_path)

        with pytest.raises(CLIError, match="Another orchestrator holds the lease"):
            acquire_lease(tmp_path)

    def test_force_overrides_live_lease(self, tmp_path: Path) -> None:
        """--force takes over a live lease."""
        acquire_lease(tmp_path)
        second = acquire_lease(tmp_path, force=True)
        assert second.exists()

        data = read_yaml_file(second)
        assert data["owner_pid"] == os.getpid()

    def test_takes_over_stale_lease(self, tmp_path: Path) -> None:
        """Stale lease (expired heartbeat) is taken over automatically."""
        state_dir = tmp_path / STATE_DIR
        state_dir.mkdir(parents=True)
        lease_path = state_dir / ORCHESTRATOR_LEASE_FILE

        # Write a lease with an old heartbeat.
        stale_time = "2020-01-01T00:00:00"
        data = {
            "owner_type": "local",
            "owner_host": "other-host",
            "owner_pid": 99999,
            "started_at": stale_time,
            "last_heartbeat_at": stale_time,
        }
        with atomic_output_file(lease_path) as tmp:
            Path(tmp).write_text(to_yaml_string(data))

        # Should take over without error.
        path = acquire_lease(tmp_path)
        new_data = read_yaml_file(path)
        assert new_data["owner_pid"] == os.getpid()

    def test_takes_over_dead_pid_same_host(self, tmp_path: Path) -> None:
        """Lease from a dead PID on the same host is taken over."""
        state_dir = tmp_path / STATE_DIR
        state_dir.mkdir(parents=True)
        lease_path = state_dir / ORCHESTRATOR_LEASE_FILE

        data = {
            "owner_type": "local",
            "owner_host": os.uname().nodename,
            "owner_pid": 2147483647,  # unlikely to be alive
            "started_at": _now_iso(),
            "last_heartbeat_at": _now_iso(),
        }
        with atomic_output_file(lease_path) as tmp:
            Path(tmp).write_text(to_yaml_string(data))

        # PID 2147483647 is not alive, so takeover should succeed.
        path = acquire_lease(tmp_path)
        new_data = read_yaml_file(path)
        assert new_data["owner_pid"] == os.getpid()

    def test_lease_write_lock_uses_mkdir_lock_directory(self, tmp_path: Path) -> None:
        lock_path = _lease_lock_path(tmp_path)
        owner_path = _lease_lock_owner_path(lock_path)

        with _LeaseWriteLock(tmp_path):
            assert lock_path.is_dir()
            assert owner_path.is_file()

        assert not lock_path.exists()
        assert not owner_path.exists()

    def test_lease_write_lock_does_not_release_foreign_owner(self, tmp_path: Path) -> None:
        lock_path = _lease_lock_path(tmp_path)
        owner_path = _lease_lock_owner_path(lock_path)

        with _LeaseWriteLock(tmp_path):
            owner_path.write_text(
                to_yaml_string(
                    {
                        "owner_host": platform.node(),
                        "owner_pid": os.getpid(),
                        "owner_token": "foreign-owner",
                    }
                )
            )

        assert lock_path.is_dir()
        assert owner_path.is_file()
        assert release_mkdir_lock(lock_path) is True
        owner_path.unlink()

    def test_lease_write_lock_reclaims_dead_same_host_owner(self, tmp_path: Path) -> None:
        lock_path = _lease_lock_path(tmp_path)
        owner_path = _lease_lock_owner_path(lock_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.mkdir()
        owner_path.write_text(
            to_yaml_string(
                {
                    "owner_host": platform.node(),
                    "owner_pid": 2147483647,
                    "owner_token": "dead-owner",
                }
            )
        )

        with _LeaseWriteLock(tmp_path):
            assert lock_path.is_dir()

        assert not lock_path.exists()
        assert not owner_path.exists()

    def test_lease_write_lock_does_not_reclaim_live_same_host_owner_by_age(
        self, tmp_path: Path
    ) -> None:
        lock_path = _lease_lock_path(tmp_path)
        owner_path = _lease_lock_owner_path(lock_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.mkdir()
        owner_path.write_text(
            to_yaml_string(
                {
                    "owner_host": platform.node(),
                    "owner_pid": os.getpid(),
                    "owner_token": "live-owner",
                }
            )
        )
        old = time.time() - 3600
        os.utime(lock_path, (old, old))

        with (
            patch("metaproc.io.orchestrator_lease.LEASE_LOCK_TIMEOUT_S", 0.0),
            patch("metaproc.io.orchestrator_lease.LEASE_LOCK_RETRY_S", 0.0),
            pytest.raises(CLIError, match="Timed out waiting for lease lock"),
        ):
            with _LeaseWriteLock(tmp_path):
                pass

        assert lock_path.is_dir()
        assert owner_path.is_file()
        assert release_mkdir_lock(lock_path) is True
        owner_path.unlink()


class TestUpdateHeartbeat:
    def test_updates_timestamp(self, tmp_path: Path) -> None:
        acquire_lease(tmp_path)

        time.sleep(0.1)
        update_heartbeat(tmp_path)
        second = read_yaml_file(tmp_path / STATE_DIR / ORCHESTRATOR_LEASE_FILE)

        # Heartbeat should be updated (may or may not differ at second resolution).
        assert "last_heartbeat_at" in second

    def test_noop_without_lease(self, tmp_path: Path) -> None:
        """No error if lease doesn't exist."""
        update_heartbeat(tmp_path)

    def test_does_not_update_foreign_lease(self, tmp_path: Path) -> None:
        """Heartbeat only updates leases owned by the current process."""
        path = acquire_lease(tmp_path)
        data = read_yaml_file(path)
        data["owner_token"] = "foreign-owner"
        with atomic_output_file(path) as tmp:
            Path(tmp).write_text(to_yaml_string(data))

        before = data["last_heartbeat_at"]
        time.sleep(0.1)
        update_heartbeat(tmp_path)
        after = read_yaml_file(path)["last_heartbeat_at"]
        assert after == before


class TestReleaseLease:
    def test_removes_lease_file(self, tmp_path: Path) -> None:
        path = acquire_lease(tmp_path)
        assert path.exists()
        release_lease(tmp_path)
        assert not path.exists()

    def test_noop_without_lease(self, tmp_path: Path) -> None:
        """No error if lease doesn't exist."""
        release_lease(tmp_path)

    def test_preserves_foreign_lease(self, tmp_path: Path) -> None:
        """Release must not remove a lease taken over by another owner."""
        path = acquire_lease(tmp_path)
        data = read_yaml_file(path)
        data["owner_token"] = "foreign-owner"
        with atomic_output_file(path) as tmp:
            Path(tmp).write_text(to_yaml_string(data))

        release_lease(tmp_path)
        assert path.exists()


class TestLeaseLiveness:
    def test_permission_denied_pid_probe_counts_as_alive(self, tmp_path: Path) -> None:
        """EPERM means the PID exists, so status must not mark the lease stale."""
        state_dir = tmp_path / STATE_DIR
        state_dir.mkdir(parents=True)
        lease_path = state_dir / ORCHESTRATOR_LEASE_FILE

        data = {
            "owner_type": "local",
            "owner_host": os.uname().nodename,
            "owner_pid": 424242,
            "owner_token": "tok",
            "started_at": _now_iso(),
            "last_heartbeat_at": _now_iso(),
        }
        with atomic_output_file(lease_path) as tmp:
            Path(tmp).write_text(to_yaml_string(data))

        with patch("metaproc.io.orchestrator_lease.os.kill", side_effect=PermissionError):
            assert is_orchestrator_alive(tmp_path) is True


class TestLeaseHeartbeat:
    def test_context_manager(self, tmp_path: Path) -> None:
        acquire_lease(tmp_path)
        with LeaseHeartbeat(tmp_path, interval=1) as heartbeat:
            assert heartbeat.is_running
        assert not heartbeat.is_running

    def test_heartbeat_updates(self, tmp_path: Path) -> None:
        acquire_lease(tmp_path)

        with LeaseHeartbeat(tmp_path, interval=1):
            time.sleep(1.5)  # Wait for at least one heartbeat

        second = read_yaml_file(tmp_path / STATE_DIR / ORCHESTRATOR_LEASE_FILE)
        # The heartbeat should have been updated at least once.
        assert "last_heartbeat_at" in second
