---
type: is
id: is-01m0t8070qf1kded17fc1tjya3
title: "PR #36 review 3B: make pool-recovery waits cancellable"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:06.165Z
updated_at: 2026-08-24T16:00:06.165Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. wait_for_pool_recovery uses blocking sleep without a cancellation predicate, which would make the future wait policy uninterruptible for up to six hours. Add cancellation before any consumer is allowed.
