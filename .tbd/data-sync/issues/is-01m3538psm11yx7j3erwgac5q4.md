---
type: is
id: is-01m3538psm11yx7j3erwgac5q4
title: Decide where probe.stderr goes; today it is written after teardown
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:40:23.988Z
updated_at: 2026-09-22T17:40:23.988Z
---
`dispatch/pool_dispatch.py:667-671` writes a forensic artifact into a directory that has
already been deleted:

```python
stderr_artifact = slot_dir / "probe.stderr"
try:
    stderr_artifact.write_text(stderr_full)
except OSError:
    pass  # slot may already be torn down on the cleanup race
```

`slot_dir` comes from `with tempfile.TemporaryDirectory(prefix="mp-probe-") as slot_str:`
opened at line 601. That block **closes at line 638**, ten lines above this write. The
directory and its whole tree are already unlinked, so `write_text` raises
`FileNotFoundError` on every execution and is swallowed every time.

Two things are wrong beyond the write itself:

- The docstring promises "Capture the full stderr to a known artifact for post-run
  inspection". That promise has never been kept.
- The comment describes a deterministic bug as a race, which is why it has survived —
  it reads as a known, tolerated condition.

This cannot be repaired by making the write atomic; the directory does not exist. Either:

- move the capture inside the `with` block *and* give it a destination outside the temp
  dir (the caller's `sidecar_target.parent` is the obvious candidate), published with
  `atomic_write_text(..., make_parents=True)`; or
- delete lines 667-671 and the comment, and rely on the head+tail summary already going
  into the note.

If the artifact starts existing it needs a documented location and a size bound —
`proc.stderr` from a runaway probe is unbounded. That is the decision this bead is
waiting on.
