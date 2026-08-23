---
type: is
id: is-01m0ragf2xm87db9bbwhv7ys57
title: Bring detached run-step under durable task lifecycle
kind: bug
status: open
priority: 2
version: 1
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r93gwcj17mn4dmw1ts7fqa
created_at: 2026-08-23T22:05:27.004Z
updated_at: 2026-08-23T22:05:27.004Z
---
Detached run-step writes a legacy launch snapshot and PID but does not create or finalize task status and per-attempt facts. Either add a durable supervisor/reaper that owns the lifecycle or explicitly restrict detached run-step to a non-resumable diagnostic surface; it must not masquerade as scheduler-managed work.
