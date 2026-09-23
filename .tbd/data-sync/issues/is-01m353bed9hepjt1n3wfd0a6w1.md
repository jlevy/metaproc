---
type: is
id: is-01m353bed9hepjt1n3wfd0a6w1
title: Bound and stop following symlinks when validating output directories
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:41:53.705Z
updated_at: 2026-09-22T17:41:53.705Z
---
`engine/validation.py:194-202` — `_directory_has_content` recurses on `child.is_dir()`,
which **follows symlinks**, with no depth bound and no cycle detection.

A symlinked directory cycle inside a declared `kind: directory` output hangs the
validator or blows the stack. Agent-produced output trees are exactly where an
unexpected symlink shows up, and this runs against them.

`filesystem-rules`: decide explicitly whether symlinks are followed, do not inherit a
library default, and detect link cycles when following directory symlinks.

Fix: `os.scandir` with `entry.is_dir(follow_symlinks=False)` and a depth bound. The
correct pattern already exists two files over — `engine/operations_summary.py`'s
`_iter_files` and `_run_size` both pass `follow_symlinks=False` explicitly and bound
depth and entry count. Use them as the reference.

Related, `dispatch/slot_coordinator.py:515`: `shutil.rmtree(lease.slot_dir)` relies on a
containment invariant enforced in a *different* module. `slot_dir_for` (`:150`) sanitizes
`step` and `item` through `_safe_path_segment` but passes `run_id` through untouched; the
real check lives in `dispatch/pool_dispatch.py:110-124`, while `acquire_slot` takes
`run_id: str` as a free parameter. A caller constructing a coordinator directly can place
— and later recursively delete — a slot directory anywhere. "Never recursively delete a
path whose exact resolved scope has not been verified" wants the assertion at the
mutation site: check `slot_dir.resolve().is_relative_to(runs_dir.resolve())` in
`teardown`, or run `run_id` through `_safe_path_segment`.
