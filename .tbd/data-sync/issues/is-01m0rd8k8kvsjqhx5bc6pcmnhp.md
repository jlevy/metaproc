---
type: is
id: is-01m0rd8k8kvsjqhx5bc6pcmnhp
title: Respect step-scoped live pools during stale-attempt reconciliation
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
created_at: 2026-08-23T22:53:34.866Z
updated_at: 2026-08-23T23:56:16.820Z
closed_at: 2026-08-23T23:56:16.820Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
reconcile_stale_running() checks only the run-level runpool-status.yaml before sweeping every task, although run-process pools persist liveness under .state/steps/<step>/runpool-status.yaml. A concurrent resume can classify active step-pool attempts as lost. Resolve liveness per task step, preserve the run-level fast path for run-parallel, invoke reconciliation once for scalar-only run-process resumes, and add live-step-pool coverage.
