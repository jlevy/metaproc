---
type: is
id: is-01m0t5d3edn704qec0gz9asyve
title: "Review PR #33: share recursive run context"
kind: task
status: in_progress
priority: 1
version: 4
labels:
  - pr-review
dependencies: []
created_at: 2026-08-24T15:14:42.764Z
updated_at: 2026-08-24T22:31:53.424Z
---
Senior review of #33 (codex/gtia-v3-execution-context, base: plan branch). Implements the RunExecutionContext consolidation (finding F1 of the #32 architecture review). Verify: semaphore unification across sibling scopes (dead external_semaphore fixed), force/skip/continue_on_error propagation handled as deliberate behavior decisions with characterization tests, no policy arg left outside the context. Post review comment; follow up on findings before merge.

## Notes

ROUND 2 (2026-08-24, head d5422ad): https://github.com/jlevy/metaproc/pull/33#issuecomment-5402358325 — R1/R3/R4/R7/R8 FIXED (R4 proven by neutralizing _leaf_slot: both ceiling tests fail). R2 deferred-with-annotation (accepted). R5 fixed in #34. R6/R9/C2 deferred+tracked. NEW must-fix: N1 executor now min(32,cpu+4) with no explicit ceiling — measured 14 concurrent vs requested max_concurrency=40 — contradicts 4 doc/help statements this PR relies on; N1b queued leaves hold permit + report running; N1c CHANGELOG wording wrong; CI has never run on this head. Should: N2 close(wait=False) releases lease before threads stop (regression vs main's bounded 300s join); N3 bare CancelledError into uncancelled tasks. Fresh: F1 leaf permit acquired before quota pause couples sibling scopes.
