---
type: is
id: is-01m0vxc03xpq9q76ae6vtapsgq
title: "PR #37 B11: unify mapped item-key derivation"
kind: task
status: closed
priority: 2
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T07:32:46.844Z
updated_at: 2026-08-25T19:28:51.600Z
closed_at: 2026-08-25T19:28:51.599Z
close_reason: Fixed with canonical ScopeIdentity binding plus key safety, duplicate, containment, and end-to-end isolation coverage.
resolution: null
duplicate_of: null
---
The mapped item key is derived in multiple places, including a step-scoped fallback that could collapse state identities if invariants drift. Route identity through the canonical ScopeIdentity path or explicitly prove the current derivations equivalent. Source: PR #37 senior review B11.

## Notes

Fixed: one contained ScopeIdentity binds child record ID, path ID, scope path, run directory, and run variables from the canonical task item key. Duplicate and unsafe keys fail before path creation; end-to-end scope isolation passes.
