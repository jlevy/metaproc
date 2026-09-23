---
type: is
id: is-01m0rax25rhvyc6weg0wmpxsm9
title: Do not strand durable attempts when auth-pool teardown fails
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r9d159n3zwmm2hxcjzq6x1
created_at: 2026-08-23T22:12:19.767Z
updated_at: 2026-08-23T23:56:16.773Z
closed_at: 2026-08-23T23:56:16.773Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
Pre-commit review finding: run-parallel classifies/finalizes a failed attempt only after _teardown_pool_slot. An exception while preserving diagnostics, tearing down the credential lease, or recording auth_outcome can unwind the scheduler with the TaskAttemptRecord still live. Make attempt terminalization fail-safe without masking the teardown error, and test the exceptional path.
