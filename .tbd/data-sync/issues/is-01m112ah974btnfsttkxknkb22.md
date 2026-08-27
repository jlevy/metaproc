---
type: is
id: is-01m112ah974btnfsttkxknkb22
title: Report plan-declared runtime coordinates missing durable state
kind: bug
status: closed
priority: 0
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - runtime-projection
dependencies: []
parent_id: is-01m0rm18kbm24khxjemevb1ybv
created_at: 2026-08-27T07:35:33.926Z
updated_at: 2026-08-27T07:52:36.383Z
closed_at: 2026-08-27T07:52:36.382Z
close_reason: "Fixed in 3f1192a: exact plan-declared scalar, mapped-item, and composite-scope coordinates now emit typed coverage gaps when durable state is absent; aliased state is rejected by identity validation. Local make verify and all five exact-head CI checks passed."
resolution: null
duplicate_of: null
---
PR #49 projection completeness: scan_task_output_projection must not silently omit a scalar task, mapped item, or composite scope declared by the exact run-plan snapshots when its durable runtime state is absent. Expose each missing declared coordinate as a generic fail-closed coverage diagnostic while preserving the fully snapshotted projection.
