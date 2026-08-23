---
type: is
id: is-01m0rd7gxz2jcp1shgy74gjyy8
title: Reconcile orphan attempt facts created before status projection
kind: bug
status: closed
priority: 1
version: 5
spec_path: docs/execution-model-design.md
labels:
  - execution-model
  - durability
dependencies: []
parent_id: is-01m0r9d159n3zwmm2hxcjzq6x1
created_at: 2026-08-23T22:52:59.710Z
updated_at: 2026-08-23T23:56:16.814Z
closed_at: 2026-08-23T23:56:16.814Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
mark_running_at() durably creates the attempt before projecting status.yaml, which is the correct pre-launch order. A crash between those atomic writes leaves a live attempt that no status points to (including first attempts with no status file). Current reconciliation scans only status.yaml, so the orphan survives, can be skipped behind an older completed status, and makes exact replay diverge. Scan task histories, classify unreferenced live attempts as lost before resume, require status to name the latest retained attempt, and add both no-status and prior-terminal crash-window tests.

## Notes

A sequential new attempt refuses to start while an earlier attempt is live, and mark_running refuses history whose latest attempt is not named by status. Normal run-process/run-parallel reconciliation closes these crash windows first; direct callers cannot create a second owner or bypass an orphan.
