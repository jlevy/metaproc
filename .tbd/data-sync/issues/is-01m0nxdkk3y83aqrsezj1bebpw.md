---
type: is
id: is-01m0nxdkk3y83aqrsezj1bebpw
title: "PR #27 review R4: the same rationale paragraph is duplicated in three places"
kind: task
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m0nxcy12vxk88gp9w80cs400
created_at: 2026-08-22T23:38:12.963Z
updated_at: 2026-08-22T23:52:43.402Z
closed_at: 2026-08-22T23:52:43.402Z
close_reason: In-code comment cut to four lines pointing at arch §14.6 and the guard; the argument lives in the doc and the test docstring. 4bf776b.
---
run_parallel.py:1035-1041, tests/test_yaml_repair.py:148-153, docs/arch/arch-metaproc-core.md:1996-1998. AGENTS.md says link to source docs rather than duplicate policy text. Compare run_process.py:1368-1369.
