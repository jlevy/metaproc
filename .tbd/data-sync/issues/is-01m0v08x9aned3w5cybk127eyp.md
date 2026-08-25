---
type: is
id: is-01m0v08x9aned3w5cybk127eyp
title: "Process: CI on stacked heads; stack and spec-change rules"
kind: task
status: open
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:16.937Z
updated_at: 2026-08-25T04:10:38.093Z
---
Keep PRs #32-#35 stacked because execution context, auth policy, and lifecycle ownership remain coherent review and rollback boundaries; PR #37 is the definitive plan and integration head. The consolidated head is the validation unit: every stack level gets exact-head CI, the combined head gets full verification and failure injection, and the pinned GTIA L0 runs before any runtime layer lands. Plan narrowings are committed on #37 and reconciled into the consumer plan, not hidden in implementation commits. Consolidate only if a boundary stops being coherent; do not impose an arbitrary stack-depth rule.

## Notes

FOURTH instance observed 2026-08-24: commit 49064f0 on #37 narrows the reviewed per-item-force contract inside the implementation PR (after ports, child namespaces, evidence pointer). Each call defensible; the pattern is the problem. Rule to adopt: plan/spec narrowings land as plan-branch commits only.
