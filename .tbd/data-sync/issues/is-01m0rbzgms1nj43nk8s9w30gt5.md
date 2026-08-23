---
type: is
id: is-01m0rbzgms1nj43nk8s9w30gt5
title: Finalize successful outputless tasks in durable history
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r9d159n3zwmm2hxcjzq6x1
created_at: 2026-08-23T22:31:08.696Z
updated_at: 2026-08-23T23:56:16.802Z
closed_at: 2026-08-23T23:56:16.802Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
Pre-commit review finding: _handle_success transitions status/attempt only inside the declared-output branch. A successful agent task with no outputs is returned as rc=0 while status and TaskAttemptRecord remain running forever, so resume and replay disagree. Finalize success and write result.yaml independently of whether outputs are declared; keep validation conditional. Add a production-path regression test.
