---
type: is
id: is-01m0r93m6cz6dytw4c1m2bbyaj
title: Add causal force, budgets, and blocker projections
kind: feature
status: open
priority: 1
version: 2
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93mr72xw9k0p8tn94a07d
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-23T21:40:57.675Z
updated_at: 2026-08-23T21:40:58.247Z
---
Implement per-task generation force with mapped invalidation, atomic attempt-keyed budget reservations, structured failure causes and one actionable blocker projection per task. Operator continue-on-error policy must never change dependency satisfaction.
