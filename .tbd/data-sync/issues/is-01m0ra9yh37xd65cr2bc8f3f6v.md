---
type: is
id: is-01m0ra9yh37xd65cr2bc8f3f6v
title: Make source-discovery test work from named Git worktrees
kind: bug
status: closed
priority: 2
version: 3
labels:
  - test-portability
dependencies: []
created_at: 2026-08-23T22:01:53.442Z
updated_at: 2026-08-23T23:56:16.827Z
closed_at: 2026-08-23T23:56:16.827Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
The GCP dispatch source-root test asserts that the checkout basename is exactly metaproc even though discovery correctly identifies roots by pyproject.toml plus src/metaproc. Normal named worktrees therefore fail the full suite. Assert the semantic root contract instead of its directory name.
