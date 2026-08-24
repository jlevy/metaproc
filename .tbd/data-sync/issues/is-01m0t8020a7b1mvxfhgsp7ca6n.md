---
type: is
id: is-01m0t8020a7b1mvxfhgsp7ca6n
title: "PR #35 review 7: emit auth outcome on cancellation teardown"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T16:00:01.034Z
updated_at: 2026-08-24T17:54:41.694Z
closed_at: 2026-08-24T17:54:41.694Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. Cancellation tears down a credential lease directly, bypassing complete_slot and leaving no auth_outcome audit event. Preserve the event trail without risking a lease leak.
