---
type: is
id: is-01m0xz1j9cxtbh6jkvdrmjtywq
title: "PR #48 review R1: remove private compatibility alias"
kind: bug
status: closed
priority: 3
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0xz18vdnee7mj9bm52x3pvk
created_at: 2026-08-26T02:40:31.018Z
updated_at: 2026-08-26T02:52:32.232Z
closed_at: 2026-08-26T02:52:32.231Z
close_reason: "PR #48 finding R1 fixed by c1ed660; exact-head CI run 32924105278 and full local verification passed; disposition published on PR"
resolution: null
duplicate_of: null
---
Formal review R1 at https://github.com/jlevy/metaproc/pull/48#pullrequestreview-5026251547. src/metaproc/commands/pool.py:30 adds a backward-compatible private alias used only by tests. Import metaproc.paths.iter_composite_run_dirs directly in tests and runtime callers, and remove the alias.
