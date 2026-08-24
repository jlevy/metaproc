---
type: is
id: is-01m0t5d3edn704qec0gz9asyve
title: "Review PR #33: share recursive run context"
kind: task
status: closed
priority: 1
version: 18
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
child_order_hints:
  - is-01m0t7zn38kj73vzyx5sv9ekrx
  - is-01m0t7znk14cmtw76xca444wgq
  - is-01m0t7zp3vhx6wk9b1j4r9px2s
  - is-01m0t7zpn7pcvzzfw9ejpn311y
  - is-01m0t7zq4sh0qqfakkecfxkh4x
  - is-01m0t7zqm3kx2kkj4m1hpnfvk4
  - is-01m0t7zr32yz748ccnexyfp2k3
  - is-01m0t7zrjkknb158w75jva83n7
  - is-01m0t7zs3etjttp22nytn7abcn
  - is-01m0t7zsk9qxhwsjdv1gc1cakx
  - is-01m0t7zt2kfff1eg1x9w8d6hq3
  - is-01m0t7zthnzm9zcqnt2bffh0rd
created_at: 2026-08-24T15:14:42.764Z
updated_at: 2026-08-24T22:32:05.452Z
closed_at: 2026-08-24T19:03:47.276Z
close_reason: "All PR #33 review findings now have explicit dispositions: R1/R3/R4/R5/R7/R8/C3 fixed, R2 fixed in the lifecycle rung, C1 rebutted, and R6/R9/C2 deferred under mp-0cyw/mp-zssw/mp-0ukj. Exact integration head c061cad passed local/pre-push verification and GitHub CI run 32765621039."
resolution: null
duplicate_of: null
---
Senior review of #33 (codex/gtia-v3-execution-context, base: plan branch). Implements the RunExecutionContext consolidation (finding F1 of the #32 architecture review). Verify: semaphore unification across sibling scopes (dead external_semaphore fixed), force/skip/continue_on_error propagation handled as deliberate behavior decisions with characterization tests, no policy arg left outside the context. Post review comment; follow up on findings before merge.

## Notes

ROUND 2 (2026-08-24, head d5422ad): https://github.com/jlevy/metaproc/pull/33#issuecomment-5402358325 — R1/R3/R4/R7/R8 FIXED (R4 proven by neutralizing _leaf_slot: both ceiling tests fail). R2 deferred-with-annotation (accepted). R5 fixed in #34. R6/R9/C2 deferred+tracked. NEW must-fix: N1 executor now min(32,cpu+4) with no explicit ceiling — measured 14 concurrent vs requested max_concurrency=40 — contradicts 4 doc/help statements this PR relies on; N1b queued leaves hold permit + report running; N1c CHANGELOG wording wrong; CI has never run on this head. Should: N2 close(wait=False) releases lease before threads stop (regression vs main's bounded 300s join); N3 bare CancelledError into uncancelled tasks. Fresh: F1 leaf permit acquired before quota pause couples sibling scopes.
