---
type: is
id: is-01m0t801gh2mz2b7vr19tg3mnj
title: "PR #35 review 6: give handlers cooperative bounded cancellation"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T16:00:00.529Z
updated_at: 2026-08-24T16:00:00.529Z
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. Synchronous handlers are shielded and drained but have no cancel_requested hook, so cancellation can wait indefinitely. Add cooperation or a bounded ownership-safe drain.
