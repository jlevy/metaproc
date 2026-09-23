---
type: is
id: is-01m0t7zxcaed845hbkanka3ktz
title: "PR #34 review 6: run quota preflight for scalar pool leaves"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3r59m3mpwg54j5s6qhf
created_at: 2026-08-24T15:59:56.297Z
updated_at: 2026-08-24T16:38:02.858Z
closed_at: 2026-08-24T16:38:02.858Z
close_reason: "Fixed in e3f177b; exact-head make verify passed (4,318 passed, 8 skipped) and disposition published on PR #34."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/34#issuecomment-5397585053. Scalar agent launches now consume the credential pool but bypass the quota preflight used by fan-out. Either add parity or explicitly defer with a documented consistency boundary.
