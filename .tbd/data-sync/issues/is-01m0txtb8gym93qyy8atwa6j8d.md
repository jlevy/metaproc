---
type: is
id: is-01m0txtb8gym93qyy8atwa6j8d
title: Skip harness preflight for adapterless active plans
kind: bug
status: closed
priority: 1
version: 6
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T22:21:22.576Z
updated_at: 2026-08-25T19:31:21.462Z
closed_at: 2026-08-25T19:31:21.462Z
close_reason: Adapter preflight now visits only active agent adapters; adapterless composite/code plans have direct coverage and full verification passes.
resolution: null
duplicate_of: null
---
A deterministic process containing only composite and code leaves must not preflight an unused default agent adapter. Filter launch preflight to active agent steps so adapterless runs have no irrelevant harness CLI side effect; preserve once-per-adapter preflight for real agent leaves and cover both cases.

## Notes

A framework-owned regression proves adapterless code and composite plans no longer invoke the default adapter preflight while actual agent steps retain preflight. Keep open until the clean consolidated head passes focused and full verification.
