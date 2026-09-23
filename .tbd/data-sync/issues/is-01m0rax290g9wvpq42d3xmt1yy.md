---
type: is
id: is-01m0rax290g9wvpq42d3xmt1yy
title: Fail closed on ambiguous or misaddressed durable attempt history
kind: bug
status: closed
priority: 1
version: 6
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r9d159n3zwmm2hxcjzq6x1
created_at: 2026-08-23T22:12:19.869Z
updated_at: 2026-08-23T23:56:16.762Z
closed_at: 2026-08-23T23:56:16.749Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
Pre-commit review finding: TaskAttemptRecord is a cross-boundary fact, but the first reader accepts duplicate attempt_number values and trace validates step/item without validating run identity. Reject duplicate ordinal histories and any record whose run_id, step_id, or item_key does not match its containing task/status before replay. Add boundary-focused tests.

## Notes

Follow-up diff review: (1) synthetic submit/prepare failures persist the canonical item_key; (2) status transitions validate run/step/item/ordinal/generation/fence against the named attempt; (3) start_attempt_at rejects history addressed to another run/step/item/key; (4) mark_running_at rejects a misaddressed legacy status or a status projection behind retained history before mutation; and (5) reconciliation fails closed on malformed status. Boundary tests cover each seam.
