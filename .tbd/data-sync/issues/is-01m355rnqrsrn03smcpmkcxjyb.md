---
type: is
id: is-01m355rnqrsrn03smcpmkcxjyb
title: "PR 92 R1: preserve existing traversal and selection order"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:04.341Z
updated_at: 2026-09-22T18:24:04.341Z
---
PR 92 review R1: engine/pathing.py:180,194 and eight traversal/order changes alter successful behavior. Remove new sorting policies; retain caught-stat-error robustness in auth_usage.
