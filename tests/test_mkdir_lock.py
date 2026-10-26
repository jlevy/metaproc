"""Tests for the shared mkdir-lock helper."""

from __future__ import annotations

import logging
import os
import time

import pytest

from metaproc.io import mkdir_lock as mkdir_lock_module
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


def test_reclaim_discards_owner_metadata_a_holder_left_behind(tmp_path):
    """A holder may record itself inside the lock; a record cannot wedge the lock."""
    lock_path = tmp_path / "resource.lock"
    lock_path.mkdir()
    (lock_path / "owner").write_text("4242 some-host\n")
    old = time.time() - 3600
    os.utime(lock_path, (old, old))

    lease = acquire_mkdir_lock(lock_path, timeout=1.0, stale_after=60.0)

    assert lock_path.is_dir()
    assert not (lock_path / "owner").exists()
    lease.release()
    assert not lock_path.exists()


def test_a_lock_path_that_is_a_symlink_is_refused_not_followed(tmp_path):
    """Deleting through a symlinked lock path would reach files outside the lock, on
    exactly the shared filesystems this module is for. It must be loud, not destructive."""
    outside = tmp_path / "outside"
    outside.mkdir()
    kept = outside / "keep.txt"
    kept.write_text("not the lock's to delete")
    lock_path = tmp_path / "resource.lock"
    lock_path.symlink_to(outside, target_is_directory=True)

    with pytest.raises(MkdirLockTimeoutError):
        acquire_mkdir_lock(lock_path, timeout=0.2, stale_after=0.0)

    assert kept.read_text() == "not the lock's to delete"
    assert lock_path.is_symlink()


class _Listing(list[os.DirEntry[str]]):
    """A directory listing that reads like `os.scandir`'s result, already consumed."""

    def __enter__(self):
        return iter(self)

    def __exit__(self, *_exc: object) -> bool:
        return False


def _swap_the_lock_during_cleanup(tmp_path, monkeypatch, lock_path, replace):
    """Run *replace* once, after the lock directory has been listed and before its
    entries are removed — the window a swap has to work in."""
    real_scandir = os.scandir
    swapped = []

    def scandir_then_swap(target):
        with real_scandir(target) as entries:
            listing = _Listing(entries)
        if not swapped:
            swapped.append(True)
            replace()
        return listing

    monkeypatch.setattr("os.scandir", scandir_then_swap)
    return swapped


def test_reclaim_refuses_to_remove_a_lock_acquired_while_it_ran(tmp_path, monkeypatch):
    """Another contender can reclaim this same lock and a new holder can take the path
    while this removal runs. Deleting that holder's record and its lock would put two
    holders in the critical section."""
    lock_path = tmp_path / "resource.lock"
    lock_path.mkdir()
    (lock_path / "owner").write_text("1 first-holder\n")

    def another_holder_takes_the_path():
        lock_path.rename(tmp_path / "evicted.lock")
        lock_path.mkdir()
        (lock_path / "owner").write_text("2 second-holder\n")

    swapped = _swap_the_lock_during_cleanup(
        tmp_path, monkeypatch, lock_path, another_holder_takes_the_path
    )

    with pytest.raises(FileExistsError):
        mkdir_lock_module._remove_lock_dir(lock_path)

    assert swapped
    assert lock_path.is_dir(), "the new holder's lock was removed"
    assert (lock_path / "owner").read_text() == "2 second-holder\n"


def test_reclaim_does_not_delete_through_a_path_swapped_for_a_symlink(tmp_path, monkeypatch):
    """Someone who can write the lock's parent can swap it for a symlink part way
    through. Children are unlinked relative to the directory this removal opened, so a
    swap cannot redirect them."""
    outside = tmp_path / "outside"
    outside.mkdir()
    kept = outside / "owner"
    kept.write_text("not the lock's to delete")
    lock_path = tmp_path / "resource.lock"
    lock_path.mkdir()
    (lock_path / "owner").write_text("1 first-holder\n")

    def swap_for_a_symlink():
        lock_path.rename(tmp_path / "moved.lock")
        lock_path.symlink_to(outside, target_is_directory=True)

    swapped = _swap_the_lock_during_cleanup(tmp_path, monkeypatch, lock_path, swap_for_a_symlink)

    with pytest.raises(OSError):
        mkdir_lock_module._remove_lock_dir(lock_path)

    assert swapped
    assert kept.read_text() == "not the lock's to delete"


def test_a_failed_reclaim_is_logged(tmp_path, caplog):
    """An acquisition that times out against a lock nobody holds is otherwise
    unexplainable, so the reclaim that could not be done says why."""
    lock_path = tmp_path / "resource.lock"
    lock_path.mkdir()
    (lock_path / "unexpected-subdir").mkdir()

    with caplog.at_level(logging.DEBUG, logger=mkdir_lock_module.__name__):
        with pytest.raises(MkdirLockTimeoutError):
            acquire_mkdir_lock(lock_path, timeout=0.1, is_stale=lambda _path: True)

    assert any("could not reclaim" in record.message for record in caplog.records)


def test_a_reclaim_that_cannot_be_done_still_reaches_the_timeout(tmp_path):
    """A lock judged stale that cannot be removed must not spin: the contender waits
    and then fails loudly, as it does for a lock held by a live process."""
    lock_path = tmp_path / "resource.lock"
    lock_path.mkdir()
    # Only regular files directly inside a lock are discarded, so this cannot be removed.
    (lock_path / "unexpected-subdir").mkdir()

    started = time.monotonic()
    with pytest.raises(MkdirLockTimeoutError):
        acquire_mkdir_lock(lock_path, timeout=0.3, is_stale=lambda _path: True)
    elapsed = time.monotonic() - started

    assert 0.25 < elapsed < 10.0, f"acquisition took {elapsed:.2f}s against a 0.3s timeout"
