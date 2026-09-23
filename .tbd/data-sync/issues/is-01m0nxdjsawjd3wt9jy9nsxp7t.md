---
type: is
id: is-01m0nxdjsawjd3wt9jy9nsxp7t
title: "PR #27 review R2: behavior change lacks a behavioral test and a CHANGELOG entry"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0nxcy12vxk88gp9w80cs400
created_at: 2026-08-22T23:38:12.137Z
updated_at: 2026-08-22T23:52:42.702Z
closed_at: 2026-08-22T23:52:42.702Z
close_reason: CHANGELOG [Unreleased] records the behavior change; the behavioral test covers the path in both directions. 4bf776b.
---
run_parallel.py:1034 and CHANGELOG.md. A mode:code fan-out step emitting unparseable frontmatter now fails validation instead of being repaired. No test executes the path; [Unreleased] is empty.
