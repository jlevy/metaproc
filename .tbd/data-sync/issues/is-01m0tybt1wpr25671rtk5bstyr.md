---
type: is
id: is-01m0tybt1wpr25671rtk5bstyr
title: "Review PR #39: deterministic scale guard"
kind: task
status: closed
priority: 2
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
child_order_hints:
  - is-01m0vmt18k4yn95zac880rb6jq
created_at: 2026-08-24T22:30:54.779Z
updated_at: 2026-09-01T05:22:10.893Z
closed_at: 2026-09-01T05:22:10.892Z
close_reason: "PR #39 merged at 262b386. Remaining scope-coverage follow-up is tracked separately as mp-gb62."
resolution: null
duplicate_of: null
---
Round-1 review posted 2026-08-24: approve-with-nits. Flake genuinely fixed (baseline exactly 3200 across seeds/interpreters, mutation reproduces at 5,121,600). Must: two-sided assertion (width <= comparisons <= ceiling) so a dead instrument cannot read as a pass. Should: new guard no longer catches loss of key_set cached_property memoization (measured 68x slowdown still passes; old guard failed at 18.62x) — this is the stack base so every rung inherits it.
