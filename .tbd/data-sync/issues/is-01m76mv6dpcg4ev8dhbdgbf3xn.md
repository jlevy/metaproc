---
type: is
id: is-01m76mv6dpcg4ev8dhbdgbf3xn
title: "PR #121 A8: run-level tests cover scalar steps only"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76msm7xe6a3n7dcegpafnrx
hold: null
hold_until: null
created_at: 2026-11-12T01:09:37.333Z
updated_at: 2026-11-12T01:09:38.303Z
started_at: 2026-11-12T01:09:37.934Z
closed_at: 2026-11-12T01:09:38.302Z
close_reason: "Fixed in fd731bd and fb335ec for the remaining scope: run-level tests for never under a scalar composite (re-runs), under a mapped composite in a child and a grandchild scope, from the step and from defaults, at launch and dry run (refused), never inherited by a mapped step of a child spec (refused, no run dir), --from alone, a satisfied override and a manual step, plus a bundle-load test. The exact_inputs shapes the finding lists were removed with the implementation (mp-gg7r)."
resolution: null
duplicate_of: null
---
Medium. tests/test_reuse_policy.py:444-490, :553-578.

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
