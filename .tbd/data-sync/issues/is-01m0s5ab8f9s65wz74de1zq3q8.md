---
type: is
id: is-01m0s5ab8f9s65wz74de1zq3q8
title: Retain process-tree ownership after the leader exits
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - review-finding
  - execution-model
dependencies: []
parent_id: is-01m0rwnp06bhsk91k8kw1szh2g
created_at: 2026-08-24T05:53:58.030Z
updated_at: 2026-08-24T05:55:24.770Z
closed_at: 2026-08-24T05:55:24.769Z
close_reason: null
resolution: null
duplicate_of: null
---
Senior review High finding: LocalBackend.kill and the sampled code-command cleanup returned early when the process leader had already exited. A stubborn descendant could therefore outlive run or host admission. Fix by treating the launch process group as the owned lifecycle on completion, timeout, and cancellation, with integration tests where the child ignores SIGTERM and the leader exits first.

## Notes

Resolved in codex/gtia-v3-cancellation-safety. LocalBackend now treats the entire POSIX process group as launch ownership even after the leader exits; sampled code commands do the same. Red/green integration tests cover normal completion and cancellation with a child that ignores SIGTERM. Full make verify: 4,283 passed, 8 skipped.
