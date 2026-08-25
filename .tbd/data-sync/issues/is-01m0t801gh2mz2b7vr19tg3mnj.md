---
type: is
id: is-01m0t801gh2mz2b7vr19tg3mnj
title: "PR #35 review 6: give handlers cooperative bounded cancellation"
kind: bug
status: closed
priority: 2
version: 5
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T16:00:00.529Z
updated_at: 2026-08-25T06:46:53.977Z
closed_at: 2026-08-25T06:46:53.975Z
close_reason: "Cooperative run-process handler cancellation and its public contract are fixed in the PR #35 stack. A bounded drain that abandons a live Python thread is rebutted because it would release ownership while artifact writes can continue; direct run-step/run-parallel do not create a second run-owned cancellation authority."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. Synchronous handlers are shielded and drained but have no cancel_requested hook, so cancellation can wait indefinitely. Add cooperation or a bounded ownership-safe drain.
