---
type: is
id: is-01m3538p1ap1awtpng65kzznea
title: Fix the host-admission slot reclaim race that over-admits
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:40:23.210Z
updated_at: 2026-09-22T17:40:23.210Z
---
`_try_acquire_slot` in `src/metaproc/runpool/host_admission.py:218-226` is
check-then-delete-then-create:

```python
try:
    slot_dir.mkdir()
except FileExistsError:
    if not self._reclaim_if_stale(slot_dir):
        return None
    try:
        slot_dir.mkdir()
    except FileExistsError:
        return None
```

`_reclaim_if_stale` ends in `_remove_slot(slot_dir)`, an `rmtree`. Two gates can read the
same stale lease and both decide to reclaim it:

1. A reads the lease, judges it stale. B reads the same lease, judges it stale.
2. A `rmtree`s, `mkdir`s, writes its lease. A now holds `slot-N`.
3. B `rmtree`s — **deleting A's live slot** — `mkdir`s, and writes its lease.

Both believe they hold `slot-N`, so the host runs above `limit`. That over-subscription
is the one thing this module exists to prevent. The token check in `release()` only
limits the damage after the fact: B's release finds A's token, logs a mismatch, and
leaks the slot.

Fix: make reclaim exclusive by winning an atomic rename rather than racing on `rmtree`.
In `_reclaim_if_stale`, replace `return _remove_slot(slot_dir)` with a rename of
`slot_dir` to a private `slot-N.reclaim-<hex>` name, and only `rmtree` what the rename
won. Exactly one process can win.

Watch the knock-on: reclaim can now leave `slot-N.reclaim-<hex>` directories behind when
the `rmtree` fails, and `list_host_admission_slots` (`host_admission.py:323`) globs
`slot-*`, which would match them. Tighten that glob or `_slot_id_from_dir` in the same
change.

Related, same file: `_read_lease_file` (`:376`) catches `FileNotFoundError`,
`json.JSONDecodeError`, and `OSError` together and returns `None`, and
`_reclaim_if_stale` treats `None` as an empty slot. A transient `EACCES`/`EIO` on a slot
held by a live, healthy process therefore reclaims it after `EMPTY_SLOT_GRACE_S`.
Separate `FileNotFoundError` (genuinely absent, reclaimable) from the rest (unknown,
never reclaim, and log).

Needs a concurrency test that fails against today's code.
