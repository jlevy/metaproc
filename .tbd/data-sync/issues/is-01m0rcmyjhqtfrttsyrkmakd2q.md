---
type: is
id: is-01m0rcmyjhqtfrttsyrkmakd2q
title: Recover status projection after an interrupted terminal attempt transition
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/execution-model-design.md
labels:
  - execution-model
  - durability
dependencies: []
parent_id: is-01m0r9d159n3zwmm2hxcjzq6x1
created_at: 2026-08-23T22:42:51.088Z
updated_at: 2026-08-23T23:56:16.807Z
closed_at: 2026-08-23T23:56:16.807Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
A crash after end_attempt_at() persists a terminal TaskAttemptRecord but before write_status_at() leaves status.yaml running. reconcile_stale_running() currently tries to finalize the same attempt as lost, which conflicts with the immutable terminal fact and can abort recovery. Reconciliation must preserve the accepted terminal attempt, project the corresponding status deterministically, and only classify genuinely live attempts as lost. Add crash-window tests for succeeded, retryable/permanent, and live attempts.
