---
type: is
id: is-01m260z10t7p5h7maqpws2x4rc
title: "PR 75 F7: review durable execution and retry-budget acceptance"
kind: task
status: open
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T16:03:42.234Z
updated_at: 2026-09-10T18:47:01.245Z
---
Review acceptance for existing durable execution work: generation/fence metadata is not a fenced publication transaction; production still writes artifacts directly and retry counters restart per invocation. Keep reference-reducer semantics distinct from production, require stale-attempt rejection, private output staging, one accepted manifest and cross-resume budget tests. Implementation stays in mp-rfnm/mp-2wtc/mp-c5wt; this child tracks the review disposition.
