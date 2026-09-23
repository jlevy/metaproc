---
type: is
id: is-01m0rb5cc2xhbc8mkmfs4vdshj
title: Make valid-output recovery compatible with immutable attempt history
kind: bug
status: closed
priority: 1
version: 4
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r9d159n3zwmm2hxcjzq6x1
created_at: 2026-08-23T22:16:52.353Z
updated_at: 2026-08-23T23:56:16.780Z
closed_at: 2026-08-23T23:56:16.780Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
Pre-commit review finding: run-parallel's pre-spawn valid-output recovery calls mark_completed_at on a previously failed status. Once that status names an immutable terminal TaskAttemptRecord, completion either tries to rewrite the failed attempt and raises or, if skipped, would make status/replay disagree. Define a fail-closed transition: accept valid outputs at the original completion seam when possible, preserve legacy recovery compatibility, and require an explicit commit/adoption fact for terminal durable failures. Add same-run and resume tests.

## Notes

Follow-up diff review: valid outputs with no status currently call mark_completed_at() and abort because there is no accepted attempt to project. Treat missing state like other unaccepted output: fall through to normal launch so stale files are cleaned and recomputed; never adopt them implicitly.
