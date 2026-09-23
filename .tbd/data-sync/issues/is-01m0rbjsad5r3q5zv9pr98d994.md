---
type: is
id: is-01m0rbjsad5r3q5zv9pr98d994
title: Delay attempt success until fan-out write-boundary validation finishes
kind: bug
status: closed
priority: 1
version: 4
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r9d159n3zwmm2hxcjzq6x1
created_at: 2026-08-23T22:24:11.596Z
updated_at: 2026-08-23T23:56:16.795Z
closed_at: 2026-08-23T23:56:16.795Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
Pre-commit review finding: local agent fan-out currently marks every item attempt succeeded inside _run_agent_pool, then _execute_fan_out_step may downgrade completed statuses after a step-wide write-boundary check. Immutable attempt facts make that downgrade raise; silently keeping success would make replay ignore a real boundary failure. Keep successful attempts live until the final boundary seam, then finalize each as succeeded or permanent, with crash-safe resume behavior and regression tests.

## Notes

Follow-up diff review: deferred finalization must treat already-completed resume items as accepted instead of raising or retroactively failing them. The step-wide boundary check must still run when another item has already failed; otherwise successful attempts can bypass their owned validator. Cover both cases.
