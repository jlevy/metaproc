---
type: is
id: is-01m0t7zvfvthf3bt667kdrwfgh
title: "PR #34 review 2: record adapter-mismatch auth skips"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3r59m3mpwg54j5s6qhf
created_at: 2026-08-24T15:59:54.363Z
updated_at: 2026-08-24T16:38:02.838Z
closed_at: 2026-08-24T16:38:02.838Z
close_reason: "Fixed in e3f177b; exact-head make verify passed (4,318 passed, 8 skipped) and disposition published on PR #34."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/34#issuecomment-5397585053. A pool/step adapter mismatch silently uses ambient credentials and emits no evidence. Emit a one-time warning or structured auth_skipped event on both scalar and fan-out paths with step and adapter identities.
