---
type: is
id: is-01m0v08x9aned3w5cybk127eyp
title: "Process: exact-head CI and spec-change rules"
kind: task
status: open
priority: 1
version: 8
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:16.937Z
updated_at: 2026-08-25T19:31:35.055Z
---
Treat execution context, credential policy, lifecycle ownership, graph propagation, and mapped scopes as explicit review and test sections inside one consolidated pull request. Require exact-head CI, full verification, failure injection, and a private downstream smoke before merge. Keep consumer-specific plans and evidence outside this public repository.

## Notes

The consolidated plan and PR structure preserve review domains in one clean surface, public/private boundaries are documented, and local exact-head verification passes. Remaining acceptance is the draft PR and final exact-head CI summary; no merge.
