---
type: is
id: is-01m3539g0x1gjf0z2mkvbpxe3q
title: Stop deleting a failed attempt output when its archive failed
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:40:49.821Z
updated_at: 2026-09-22T17:40:49.821Z
---
`commands/run_parallel.py:1390-1416` archives a failed attempt's output before a retry
wipes it. When the archive fails, the wipe happens anyway.

```python
try:
    attempts_root.mkdir(parents=True, exist_ok=True)
    archived = attempts_root / stale.name
    if stale.is_dir():
        if archived.exists():
            shutil.rmtree(archived)
        shutil.copytree(stale, archived)
    else:
        shutil.copy2(stale, archived)
except OSError as snap_err:
    log.warning("Could not snapshot failed attempt %s to %s: %s", ...)
log.info("Cleaning stale output before retry: %s", stale)
if stale.is_dir():
    shutil.rmtree(stale)
else:
    stale.unlink()
```

The `except` logs and falls through. The delete is not in an `else`, so a failed
snapshot is followed by an unconditional destroy of the only copy. The block's own
comment says the archive exists because otherwise "the actual failed content is
permanently lost" — which is precisely what happens on the path the comment is warning
about. The retry proceeds, and the run's exit status says nothing.

`filesystem-rules`, "Report Partial Failure Honestly": an exit code of zero after a
batch in which targets failed is a bug in the same class as data loss.

The `try` is labelled "Best-effort — never block the retry on archive failure", so this
is a deliberate trade as written. The decision this bead needs is which of the two is
actually wanted:

1. Only delete when the snapshot succeeded (`else:` on the `try`). Costs: a full disk
   now blocks a retry instead of silently discarding evidence.
2. Keep the best-effort delete, but surface the failure in the item's failure record
   rather than only in a log line, so an operator reading the run knows the post-mortem
   is gone.

Two adjacent defects in the same block, both worth fixing whichever way the above goes:

- **The archive itself is not atomic** (`:1401-1404`): `rmtree(archived)` then
  `copytree(stale, archived)` means a crash leaves the previous archive deleted and the
  new one half-copied. `strif.copytree_atomic(stale, archived, make_parents=True)` stages
  beside the destination and commits by rename. Note `copytree_atomic` defaults
  `symlinks=False`, matching `shutil.copytree`. For the file arm, `copyfile_atomic` does
  not promise `copy2`'s mtime/mode preservation — if the archive is meant to carry the
  failed artifact's timestamps, add an explicit `shutil.copystat` after the commit and
  say so.
- **Recursive delete of an unverified scope** (`:1401, 1414`): `shutil.rmtree` acts on
  paths from `resolve_record_output_paths(effective_outputs, resolve_vars)` — operator-
  authored output templates with variable substitution. Nothing checks the resolved path
  is contained under `shared["item_dir"]` before recursing. `filesystem-rules` is
  explicit: never recursively delete a path whose exact resolved scope has not been
  verified. A template resolving to `.` or an absolute root would be catastrophic.
  Assert containment immediately before the delete, not at plan time.
