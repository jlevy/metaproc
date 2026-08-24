---
type: is
id: is-01m0v08xkcyw3vsqp02kyhpn5h
title: "Design: one attempt-lifecycle scope owning acquire/release order"
kind: task
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:17.259Z
updated_at: 2026-08-24T23:04:17.259Z
---
Structural remedy for the recurring leak class: permit → host admission → credential lease → state write → launch → terminal write → release is hand-woven with bespoke try/except at the scalar, pool, and mapped paths, and every leak across two review rounds was an ordering or exception-path mistake in one of the copies. Build one AsyncExitStack-shaped attempt scope that guarantees release + terminal state on every exit path including BaseException, and make it the only way executors launch work (the evidenced half of rev3 P8 governance). Holistic section 4b.
