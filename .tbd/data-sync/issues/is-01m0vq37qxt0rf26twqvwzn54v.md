---
type: is
id: is-01m0vq37qxt0rf26twqvwzn54v
title: "PR #34 R2 F1: enforce run-parallel pool scope containment"
kind: bug
status: closed
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3r59m3mpwg54j5s6qhf
created_at: 2026-08-25T05:43:08.284Z
updated_at: 2026-08-25T05:50:38.963Z
closed_at: 2026-08-25T05:50:38.963Z
close_reason: null
resolution: null
duplicate_of: null
---
Direct and worker run-parallel construct PoolDispatchConfig from RUN_ID without deriving a path-relative scope, so '..' can escape RUNS_DIR and composite worker evidence can disagree with run-process. Reuse one lexical scope-binding primitive in both paths and add a traversal regression test. Review: https://github.com/jlevy/metaproc/pull/34#issuecomment-5402358604
