---
type: is
id: is-01m0yzwdz90x505k59tq1w73xa
title: Honor root concurrency across nested executable leaves
kind: bug
status: in_progress
priority: 0
version: 2
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-26T12:14:25.767Z
updated_at: 2026-08-26T12:14:31.418Z
---
A process configured with a single run-wide concurrency slot can still launch sibling agent leaves concurrently when they are reached through mapped or composite scopes. Add a deterministic provider-free regression, identify the ownership break, and route every nested executable leaf through the existing run-owned admission authority. Keep scopes slot-free and do not introduce a second scheduler or controller.
