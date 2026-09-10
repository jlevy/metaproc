---
type: is
id: is-01m0s7cvkpk6pmhzenf9stzmgr
title: Keep pool admission waits out of execution attempt history
kind: bug
status: closed
priority: 1
version: 12
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
hold: null
hold_until: null
created_at: 2026-08-24T06:30:17.461Z
updated_at: 2026-09-10T18:48:55.112Z
closed_at: 2026-09-10T18:48:55.111Z
close_reason: Same admission-wait attempt-history defect as mp-ux0f. Its typed PoolSlotUnavailableError ordering, fail-fast/wait/signal outcomes, scalar/fan-out tests, and prohibition on speculative retry-later/scheduler policy are preserved in mp-ux0f and the execution plan. No runtime fix is claimed; mp-ux0f and PR75 F8 mp-skoo remain open.
resolution: duplicate
duplicate_of: is-01m0rbq50a74x57jtyp35zrkwh
---
A fan-out prepare-launch PoolSlotUnavailableError currently creates a synthetic failed TaskAttemptRecord and then reschedules the same attempt. Handle typed pool exhaustion before synthetic launch-failure state so fail-fast, wait, and signal admission outcomes never become execution attempts. Cover scalar and fan-out paths.

## Notes

This is an integration correction at the current TaskAttemptRecord boundary. Pool admission has not launched work, so reuse the existing typed PoolSlotUnavailableError path without inventing a new attempt or scheduler state machine.
