---
type: is
id: is-01m10nrk9yd563zz35e2zt4yag
title: Anchor recorded child scopes to the declared parent plan
kind: bug
status: closed
priority: 0
version: 2
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
  - runtime-projection
dependencies: []
parent_id: is-01m10mm4vpgbqgrjqx4dbjee41
created_at: 2026-08-27T03:56:03.262Z
updated_at: 2026-08-27T04:36:02.228Z
closed_at: 2026-08-27T04:36:02.215Z
close_reason: "Fixed in 9d34c1f; full make verify passed with 4,493 tests and GitHub CI completed 5/5 green. Published per-finding dispositions on PR #49."
resolution: null
duplicate_of: null
---
A review found that a nested run-plan record could self-authorize any structurally placed .state scope. Require every child snapshot to match a declared parent composite step and the current scalar/mapped path shape. Add undeclared, removed, and shape-drift regressions. Disposition target: fixed in the runtime projection without a second scope registry.
