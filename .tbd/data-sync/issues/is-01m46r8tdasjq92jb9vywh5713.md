---
type: is
id: is-01m46r8tdasjq92jb9vywh5713
title: Retire queued retry accounting when a shared-pool scheduler exits
kind: bug
status: in_progress
priority: 2
version: 3
delegate: codex@spud10.local
labels: []
dependencies: []
hold: null
hold_until: null
created_at: 2026-10-05T19:22:24.026Z
updated_at: 2026-10-05T19:29:44.629Z
started_at: 2026-10-05T19:22:38.600Z
---

## Notes

Review reproduced a shared-pool scheduler leaving its queued retry counted after cancellation. With one independent sibling retry, the regression observed pending_retries=2 instead of 1. The scheduler now retires only its remaining retry heap after finalizing active submissions. Consumers checked: pool status and pending-retry reporting, run-status liveness, governor retry-pressure sampling, and persisted scale-state. The focused scheduler/admission suite passes. Lower-layer fix awaits review, commit, and CI.
