---
type: is
id: is-01m0nxdm00g5e2qztg32ew6t57
title: "PR #27 review R5: failing guard assertion dumps the whole module source"
kind: task
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m0nxcy12vxk88gp9w80cs400
created_at: 2026-08-22T23:38:13.375Z
updated_at: 2026-08-22T23:52:43.759Z
closed_at: 2026-08-22T23:52:43.759Z
close_reason: Assertions compute their boolean before asserting, so a failure shows the message rather than the module source. 4bf776b.
---
tests/test_yaml_repair.py:172,178. pytest rewriting prints both operands; the explanatory message scrolls away behind ~2100 lines. Fix: compute the boolean first.
