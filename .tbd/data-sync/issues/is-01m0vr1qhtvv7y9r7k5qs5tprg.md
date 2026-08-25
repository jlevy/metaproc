---
type: is
id: is-01m0vr1qhtvv7y9r7k5qs5tprg
title: "PR #35 R2 F6: avoid default-executor contention for filter joins"
kind: bug
status: open
priority: 3
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-25T05:59:47.513Z
updated_at: 2026-08-25T06:46:54.224Z
---
Filter-thread joins moved off the event loop via asyncio.to_thread but now share the default executor with unrelated framework work. Decide whether the existing run-owned executor is the simpler authority; fix or explicitly defer based on measured contention.

## Notes

Deferred in PR #35 disposition comment 5406589771. Keep asyncio.to_thread on the standard executor until smoke or telemetry demonstrates filter-join contention. Escalation trigger: measured launch/completion delay attributable to queued filter joins. Do not add a dedicated executor and shutdown lifecycle without that evidence.
