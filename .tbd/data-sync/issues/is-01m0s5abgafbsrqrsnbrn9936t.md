---
type: is
id: is-01m0s5abgafbsrqrsnbrn9936t
title: Fail explicitly when process-group cleanup cannot be proven
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - review-finding
  - error-handling
dependencies: []
parent_id: is-01m0rwnp06bhsk91k8kw1szh2g
created_at: 2026-08-24T05:53:58.281Z
updated_at: 2026-08-24T05:55:24.777Z
closed_at: 2026-08-24T05:55:24.777Z
close_reason: null
resolution: null
duplicate_of: null
---
Senior review High finding: the final SIGKILL timeout only logged an error, then allowed the caller to release capacity. Fix by raising an operational failure and by preserving executor cleanup failures instead of replacing them with ordinary cancellation.

## Notes

Resolved in codex/gtia-v3-cancellation-safety. A process group that remains live after SIGKILL now raises RuntimeError, and _run_sync preserves cleanup failures rather than replacing them with ordinary cancellation. Lint, BasedPyright, docs, supply-chain, distribution, and full tests are green.
