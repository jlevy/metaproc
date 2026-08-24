---
type: is
id: is-01m0tcw3acmhtkj35g8thzxrmd
title: "PR #35 self-review: auth event failure must not leak a late lease"
kind: bug
status: closed
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T17:25:14.187Z
updated_at: 2026-08-24T17:54:41.732Z
closed_at: 2026-08-24T17:54:41.732Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
The cancellation callback logs auth_lease_acquired before complete_slot. If event transport raises, credential teardown is skipped. Make ownership cleanup unconditional and add failure-path coverage while preserving the original cancellation.
