---
type: is
id: is-01m0r93m6cz6dytw4c1m2bbyaj
title: Add cross-scope causal force, budgets, and blocker projections
kind: feature
status: open
priority: 2
version: 6
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-23T21:40:57.675Z
updated_at: 2026-08-24T02:23:14.811Z
---
Follow-on after the general scheduler: add generation-based invalidation across mapped scope boundaries, atomic attempt-keyed budget reservations, structured failure causes, and one actionable blocker projection per task. Within-scope per-item force is owned by mp-0ukj and is a mapped-scope prerequisite, not deferred here. Operator continue-on-error policy must never change dependency satisfaction.
