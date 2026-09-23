---
type: is
id: is-01m0t7zwwvhe9a6mgykq8h2f5x
title: "PR #34 review 5: wrap PoolAuthOverrideError for operators"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3r59m3mpwg54j5s6qhf
created_at: 2026-08-24T15:59:55.803Z
updated_at: 2026-08-24T16:38:02.852Z
closed_at: 2026-08-24T16:38:02.852Z
close_reason: "Fixed in e3f177b; exact-head make verify passed (4,318 passed, 8 skipped) and disposition published on PR #34."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/34#issuecomment-5397585053. compose_slot_env can raise raw PoolAuthOverrideError through gather. Preserve lease cleanup but translate the expected configuration failure to CLIError with a usable message.
