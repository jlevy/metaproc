---
type: is
id: is-01m0nabs4z4qgsbwde4mdh5fmf
title: Unify validate+classify+retry idiom across executors (R5 remainder)
kind: task
status: open
priority: 3
version: 1
labels: []
dependencies: []
created_at: 2026-08-22T18:05:10.175Z
updated_at: 2026-08-22T18:05:10.175Z
---
PR #25 review R5 (deferred remainder): after repair_declared_outputs unification, the validate->classify->capped-retry sequence still exists in run_process._execute_agent_step, run_parallel code-mode, and the pool path. Consider one shared per-attempt helper. Also: retry.py:348 mentions METAPROC_MAX_CONTENT_RETRIES which does not exist (S2) — drop or implement while in there.
