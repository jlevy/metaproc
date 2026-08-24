---
type: is
id: is-01m0nxdk6349sw3fzm4srr561j
title: "PR #27 review R3: conform scoping is claimed by the doc but asserted nowhere"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0nxcy12vxk88gp9w80cs400
created_at: 2026-08-22T23:38:12.546Z
updated_at: 2026-08-22T23:52:43.059Z
closed_at: 2026-08-22T23:52:43.058Z
close_reason: The parse-tree guard covers conform_declared_outputs alongside repair on both executors' code branches. 4bf776b.
---
docs/arch/arch-metaproc-core.md:1992,2000 and tests/test_schema_conform.py. Only repair has a guard; conform_declared_outputs on the code branch passes every test.
