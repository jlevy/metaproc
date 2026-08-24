---
type: is
id: is-01m0s7cwsmee3fj0gehfkfz329
title: Represent deferred process state and retry-later events explicitly
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
created_at: 2026-08-24T06:30:18.675Z
updated_at: 2026-08-24T06:30:18.675Z
---
StepStatus documents deferred and the auth architecture documents retry_later events, but dispatch currently has no deferred transition or event. A signal checkpoint must leave process status inspectably deferred, not running/failed, and emit a typed event without pretending the process completed.
