---
type: is
id: is-01m11004dk6p6nh3nhfheqyswg
title: Allow runtime projection scans from exact snapshots alone
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - runtime-projection
dependencies: []
parent_id: is-01m0rm18kbm24khxjemevb1ybv
created_at: 2026-08-27T06:54:55.923Z
updated_at: 2026-08-27T07:02:48.449Z
closed_at: 2026-08-27T07:02:48.448Z
close_reason: "Fixed in 0af3967: exact per-scope snapshots now support bundle-free scans, missing root or declared-child snapshots fail closed, 59 focused tests and the 4,497-test full gate pass, and all five PR checks are green."
resolution: null
duplicate_of: null
---
Modern runs persist exact per-scope RunPlanSnapshot records, but scan_task_output_projection still requires a PlanBundle even though that bundle is used only for process-name checking and legacy fallback. Make the bundle optional, fail closed when a required scope snapshot is absent without a fallback bundle, and retain the existing legacy path when a bundle is supplied. Gate: a synthetic root plus nested snapshot run projects with an unavailable authored process source; missing root and declared-child snapshots raise actionable errors.
