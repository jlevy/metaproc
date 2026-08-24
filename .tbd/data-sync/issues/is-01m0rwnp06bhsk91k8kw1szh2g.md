---
type: is
id: is-01m0rwnp06bhsk91k8kw1szh2g
title: Prove recursive cancellation and cancellation-safe leaf admission
kind: task
status: open
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0rs7df0g28zgnsykar366kb
created_at: 2026-08-24T03:22:52.293Z
updated_at: 2026-08-24T04:43:10.996Z
---
Add deterministic end-to-end tests for cancellation responsiveness and prove that shared leaf permits remain truthful until executor or subprocess supervision has terminated. The normal-operation recursive sibling ceiling landed in 30644fd.

## Notes

Senior review scope: cover cancellation while a blocking credential acquisition is running in the run-owned executor. Cancellation can arrive before the await assigns the returned SlotLease, so the implementation must either shield acquisition until its result can be torn down or provide equivalent ownership transfer; prove no slot directory, Vehicle B label lock, active counter, run permit, host admission, executor task, or subprocess survives cancellation.
