---
type: is
id: is-01m0t7zwe05cby0cp08dwf6z5q
title: "PR #34 review 4: close completion-window auth lease leak"
kind: bug
status: closed
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3r59m3mpwg54j5s6qhf
created_at: 2026-08-24T15:59:55.328Z
updated_at: 2026-08-24T17:54:41.720Z
closed_at: 2026-08-24T17:54:41.720Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/34#issuecomment-5397585053. _complete_auth_attempt clears the lease before awaiting complete_slot, so cancellation during completion bypasses outer teardown and leaks the slot/lock until stale reclaim. Preserve ownership until completion is cancellation-safe.

## Notes

Deferred disposition published on PR #34 at https://github.com/jlevy/metaproc/pull/34#issuecomment-5398315253. Address on PR #35 with cancellation-safe completion ownership; do not close until a cancellation-during-complete_slot test proves teardown.
