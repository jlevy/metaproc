---
type: is
id: is-01m355rnqrsrn03smcpmkcxjyb
title: "PR 92 R1: preserve existing traversal and selection order"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:04.341Z
updated_at: 2026-09-22T18:59:41.216Z
closed_at: 2026-09-22T18:59:41.194Z
close_reason: Fixed in e908f70. All review dispositions posted on PR 92; full make verify passed (5069 passed, 34 skipped) and GitHub lint, distribution, and Python 3.12-3.14 CI passed. PR 92 merged as 7de48d1 into claude/collect-input-reuse.
resolution: null
duplicate_of: null
---
PR 92 review R1: engine/pathing.py:180,194 and eight traversal/order changes alter successful behavior. Remove new sorting policies; retain caught-stat-error robustness in auth_usage.
