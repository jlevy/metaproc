---
type: is
id: is-01m0r7j16gbqngg6w47bktqz74
title: "PR #29 review R1: retain actionable facts under feedback size bounds"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m0r7hpfe75sqfg3vecc7j8fr
created_at: 2026-08-23T21:13:52.592Z
updated_at: 2026-08-23T21:26:27.867Z
closed_at: 2026-08-23T21:26:27.866Z
close_reason: null
---
src/metaproc/engine/retry.py:107. An oversized first OutputFailure can consume the total budget, emit only omitted_failures, and suppress smaller later failures. Bound field representations or degrade to a minimal structured summary; cover control-heavy first failure, later normal failure, and exact ceiling.
