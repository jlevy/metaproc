---
type: is
id: is-01m0t5d4f3vpf2e815ryxxqkp7
title: "Review PR #36: transport retry-later policy"
kind: task
status: in_progress
priority: 1
version: 12
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
child_order_hints:
  - is-01m0t804b6wkqyjrzk3nwpwnv3
  - is-01m0t8055d9ryetyv8axef8cqn
  - is-01m0t8068yxh3je3xnfrq8f70k
  - is-01m0t8070qf1kded17fc1tjya3
  - is-01m0t807ptgkcbemssgq9qzx38
  - is-01m0t808bang7nryyzzhtg6phy
  - is-01m0t808zq4fjshwjtvhs7zn34
  - is-01m0t809p8h99cy79t3hwd3j1f
created_at: 2026-08-24T15:14:43.810Z
updated_at: 2026-08-24T16:00:08.902Z
---
Senior review of #36 (codex/gtia-v3-retry-later, draft). Retry-later policy across cloud dispatch, entrypoints, env vars, auth-pool flags. Verify env-var plumbing, flag compatibility, cloud entrypoint behavior. Post review comment; follow up before merge.

## Notes

Review posted 2026-08-24: https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537 — verdict: needs work before undraft (draft gate itself correct). Before undraft: (1) defaults transported instead of omitted → spurious dispatch_config_change on every resume of old runs + worker-image CLI skew; make CLI defaults '' and map ''→canonical in from_strings + no-emission test; (2) false statements: arch-authentication.md:973-975 'do not yet expose', pool_dispatch.py:88 'is consulted' (policy inert: acquire_slot raises unconditionally, wait_for_pool_recovery zero callers), preflight.py:311 recommends no-op remedy; missing CHANGELOG; (3) validate options unconditionally + add cancellation predicate to wait_for_pool_recovery now. Design seams for convergence slices: rival hardcoded fan-out cooling loop (run_parallel.py:1913-1932, string-matched, unbounded), capacity-held-across-wait, dual policy representations in context, no max_wait bound vs Batch walltime, duplicate duration parsers. FOLLOW UP: track convergence slices under mp-tibt.
