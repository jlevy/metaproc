---
type: is
id: is-01m0rbgdrjj6tcencs8zzekacw
title: Persist actual retry dispositions at every execution seam
kind: bug
status: closed
priority: 1
version: 4
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r9d159n3zwmm2hxcjzq6x1
created_at: 2026-08-23T22:22:54.225Z
updated_at: 2026-08-23T23:56:16.787Z
closed_at: 2026-08-23T23:56:16.787Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
Pre-commit review finding: several production paths write a terminal disposition that does not match the scheduler decision. Scalar code can record retryable even though it has no retry executor; fan-out code needs retryable only when its declared wrapper owns a budget; non-fan-out agent paths record permanent when a retryable failure exhausts its budget. Audit each mark_failed transition, pass the actual verdict, and test exhausted as well as successful retries.

## Notes

Follow-up diff review: pool failures first write status.yaml with attempt_disposition=None, then _finish_attempt only updates the durable attempt. The final status therefore omits the classifier's failure_class (and can disagree with other terminal fields). Finalization must project the accepted terminal attempt back to status after the immutable fact is written; add retryable/permanent pool assertions.
