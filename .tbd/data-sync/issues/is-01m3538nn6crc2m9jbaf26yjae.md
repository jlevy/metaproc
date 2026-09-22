---
type: is
id: is-01m3538nn6crc2m9jbaf26yjae
title: Fix the mkdir-lock stale-reclaim race that breaks mutual exclusion
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:40:22.822Z
updated_at: 2026-09-22T17:40:22.822Z
---
`acquire_mkdir_lock` claims the lock with a bare `lock_path.mkdir()`, which is the right
`O_EXCL`-style primitive. The defect is in the stale-reclaim arm at
`src/metaproc/io/mkdir_lock.py:134-146`: on `FileExistsError` it asks
`_lock_should_be_reclaimed`, then unconditionally `rmdir`s the holder's directory and
loops.

Nothing ties the directory it removes to the directory it judged. Two contenders A and
B can both decide holder H is stale:

1. A judges H stale. B judges H stale.
2. A `rmdir`s H's lock, `mkdir`s its own, and proceeds believing it holds the lock.
3. B `rmdir`s **A's freshly created lock**, `mkdir`s its own, and proceeds believing the
   same.

Both hold the lock. `release_mkdir_lock` (`mkdir_lock.py:180`) has the mirror of this:
an evicted holder's release removes the *new* holder's directory.

`io/claimed_items.py` takes the default age-based path (`stale_after=60`), so concurrent
item claiming is exposed to this.

Fix: make reclaim a compare-and-swap on identity rather than an unconditional remove.
Have `_lock_should_be_reclaimed` return the `os.stat_result` it judged, re-`stat`
immediately before removal, and skip when `(st_ino, st_mtime_ns)` differs. Winning an
atomic rename to a private name is stronger still — only one process can win it, and the
loser learns it lost:

```python
doomed = lock_path.with_name(f"{lock_path.name}.reclaim-{uuid.uuid4().hex}")
try:
    os.rename(lock_path, doomed)
except OSError:
    continue          # someone else won the reclaim
shutil.rmtree(doomed, ignore_errors=True)
```

`io/orchestrator_lease.py` is the in-repo reference for the safer shape: its `is_stale`
reads an owner file and falls back to a 5s age floor, so a just-created lock is never
judged stale.

Needs a concurrency test that fails against today's code.
