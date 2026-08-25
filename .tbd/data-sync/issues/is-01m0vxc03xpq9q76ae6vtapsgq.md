---
type: is
id: is-01m0vxc03xpq9q76ae6vtapsgq
title: "PR #37 B11: unify mapped item-key derivation"
kind: task
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T07:32:46.844Z
updated_at: 2026-08-25T07:32:46.844Z
---
The mapped item key is derived in multiple places, including a step-scoped fallback that could collapse state identities if invariants drift. Route identity through the canonical ScopeIdentity path or explicitly prove the current derivations equivalent. Source: PR #37 senior review B11.
