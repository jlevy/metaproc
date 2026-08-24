---
type: is
id: is-01m0t8020a7b1mvxfhgsp7ca6n
title: "PR #35 review 7: emit auth outcome on cancellation teardown"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T16:00:01.034Z
updated_at: 2026-08-24T16:00:01.034Z
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. Cancellation tears down a credential lease directly, bypassing complete_slot and leaving no auth_outcome audit event. Preserve the event trail without risking a lease leak.
