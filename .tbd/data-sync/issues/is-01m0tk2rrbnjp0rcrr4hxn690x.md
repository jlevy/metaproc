---
type: is
id: is-01m0tk2rrbnjp0rcrr4hxn690x
title: "PR #37 review R3: document mapped composite M0 behavior"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m0tk2b98fs83zr2q0f6gc7rt
created_at: 2026-08-24T19:13:44.203Z
updated_at: 2026-08-24T19:31:53.626Z
closed_at: 2026-08-24T19:31:53.626Z
close_reason: "Addressed on PR #37 at 74e0c2e. R1 duplicate mapped keys fixed and tested before state writes; R3 shipped docs and active plan synchronized; R2 was separately rebutted because ProcessStep already rejects the alleged shape. Exact-head GitHub CI run 32768441674 passed all five jobs and the disposition map is published at issuecomment-5400278580."
resolution: null
duplicate_of: null
---
Formal review https://github.com/jlevy/metaproc/pull/37#pullrequestreview-5011619572. Shipped docs still state composites cannot fan out and prescribe child CLI handlers. Update CHANGELOG, core/concepts/operator/P8 docs for only the released M0 behavior and disclose scalar child-output validation.
