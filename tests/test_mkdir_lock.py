"""Tests for the shared mkdir-lock helper."""

from __future__ import annotations

import os
import time

import pytest

from metaproc.io.mkdir_lock import (
    MkdirLockTimeoutError,
    acquire_mkdir_lock,
    mkdir_lock,
)


def test_context_manager_releases_lock(tmp_path):
    lock_path = tmp_path / "resource.lock"

    with mkdir_lock(lock_path):
        assert lock_path.is_dir()

    assert not lock_path.exists()


def test_split_lease_release_unlocks_path(tmp_path):
    lock_path = tmp_path / "resource.lock"

    lease = acquire_mkdir_lock(lock_path)
    assert lock_path.is_dir()
    lease.release()

    assert not lock_path.exists()


def test_split_lease_times_out_when_lock_is_held(tmp_path):
    lock_path = tmp_path / "resource.lock"

    with mkdir_lock(lock_path), pytest.raises(MkdirLockTimeoutError):
        acquire_mkdir_lock(lock_path, timeout=0.0)


def test_stale_after_none_never_reclaims_by_age(tmp_path):
    lock_path = tmp_path / "resource.lock"
    lock_path.mkdir()
    old = time.time() - 3600
    os.utime(lock_path, (old, old))

    with pytest.raises(MkdirLockTimeoutError):
        acquire_mkdir_lock(lock_path, timeout=0.0, stale_after=None)

    assert lock_path.is_dir()


def test_custom_stale_predicate_controls_reclaim(tmp_path):
    lock_path = tmp_path / "resource.lock"
    lock_path.mkdir()

    lease = acquire_mkdir_lock(lock_path, timeout=0.0, is_stale=lambda path: path.exists())

    assert lock_path.is_dir()
    lease.release()
