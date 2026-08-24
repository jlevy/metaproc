---
type: is
id: is-01m0tk2qs4nm8x598sgd3fa3tj
title: "PR #37 review R1: reject duplicate mapped item keys"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m0tk2b98fs83zr2q0f6gc7rt
created_at: 2026-08-24T19:13:43.202Z
updated_at: 2026-08-24T19:31:53.618Z
closed_at: 2026-08-24T19:31:53.617Z
close_reason: "Addressed on PR #37 at 74e0c2e. R1 duplicate mapped keys fixed and tested before state writes; R3 shipped docs and active plan synchronized; R2 was separately rebutted because ProcessStep already rejects the alleged shape. Exact-head GitHub CI run 32768441674 passed all five jobs and the disposition map is published at issuecomment-5400278580."
resolution: null
duplicate_of: null
---
Formal review https://github.com/jlevy/metaproc/pull/37#pullrequestreview-5011619572. discovery.py:105-132 and run_process.py:2360-2373 allow two roster rows resolving to one for_each.key to race on the same task state and child scope. Validate uniqueness in neutral discovery before execution and test deterministic failure with no state writes.
