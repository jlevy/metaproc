---
type: is
id: is-01m0v08x9aned3w5cybk127eyp
title: "Process: exact-head CI and spec-change rules"
kind: task
status: open
priority: 1
version: 5
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:16.937Z
updated_at: 2026-08-25T17:02:56.379Z
---
Treat execution context, credential policy, lifecycle ownership, graph propagation, and mapped scopes as explicit review and test sections inside one consolidated pull request. Require exact-head CI, full verification, failure injection, and a private downstream smoke before merge. Keep consumer-specific plans and evidence outside this public repository.

## Notes

The former stacked structure is superseded by one clean review surface because the half-feature boundaries obscured the executable design. Preserve the conceptual failure domains and per-finding dispositions inside the replacement review.
