---
type: is
id: is-01m0s7cw0ghtj387wj4nar45we
title: Bound retry-later waits without holding launch capacity
kind: bug
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
created_at: 2026-08-24T06:30:17.872Z
updated_at: 2026-08-24T06:30:17.872Z
---
Wait mode must release run/host launch capacity while credentials are unavailable, use deterministic asynchronous recovery delays, and stop at auth-retry-max-wait without jitter overshoot. Cover scalar and fan-out behavior with injected clocks/sleepers.
