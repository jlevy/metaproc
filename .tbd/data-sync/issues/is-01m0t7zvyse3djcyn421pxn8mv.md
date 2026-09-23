---
type: is
id: is-01m0t7zvyse3djcyn421pxn8mv
title: "PR #34 review 3: persist scalar pool-exhaustion failure state"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3r59m3mpwg54j5s6qhf
created_at: 2026-08-24T15:59:54.841Z
updated_at: 2026-08-24T16:38:02.845Z
closed_at: 2026-08-24T16:38:02.845Z
close_reason: "Fixed in e3f177b; exact-head make verify passed (4,318 passed, 8 skipped) and disposition published on PR #34."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/34#issuecomment-5397585053. Top-level scalar PoolSlotUnavailableError aborts orchestration with process-status still running. Catch at the step boundary, write failed attempt/status and step_fail evidence; full retry-later parity may remain deferred.
