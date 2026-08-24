---
type: is
id: is-01m0s7cvkpk6pmhzenf9stzmgr
title: Keep pool admission waits out of execution attempt history
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
created_at: 2026-08-24T06:30:17.461Z
updated_at: 2026-08-24T06:30:17.461Z
---
A fan-out prepare-launch PoolSlotUnavailableError currently creates a synthetic failed TaskAttemptRecord and then reschedules the same attempt. Handle typed pool exhaustion before synthetic launch-failure state so fail-fast, wait, and signal admission outcomes never become execution attempts. Cover scalar and fan-out paths.
