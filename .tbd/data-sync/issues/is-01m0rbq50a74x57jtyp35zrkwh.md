---
type: is
id: is-01m0rbq50a74x57jtyp35zrkwh
title: Do not persist pool-capacity waits as execution attempts
kind: bug
status: open
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m260z1a9y9q4t6504vta2syt
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-08-23T22:26:34.633Z
updated_at: 2026-09-10T18:48:31.244Z
---
Pre-commit review finding: _build_prepare_launch writes mark_running before credential-slot admission, and launch/preparation failures synthesize TaskAttemptRecords. The pool-exhausted path explicitly does not consume the production retry budget, while replay counts every lost record against max_attempts, so repeated admission waits can replay as failed even while production keeps waiting. Move task state through admission_wait without an attempt fact; create AttemptStarted only after a launch claim is admitted. Separately encode class-specific quota retry policy so replay and resume enforce the same budget.

## Notes

PR75 F8 acceptance is governed by the active execution plan. Consolidate mp-f5m5 here: catch typed PoolSlotUnavailableError before synthetic launch-failure/TaskAttemptRecord construction; cover fail-fast, wait, and signal outcomes in scalar and fan-out paths; preserve retry-budget agreement between production, replay, and resume. Use bounded typed gate/reason/elapsed/ownership facts and create execution attempts only at actual admitted launch. Reuse the current attempt boundary; no new scheduler or dormant retry-later policy is authorized. The source duplicate was paused as part of the retry-later cluster; its no-speculative-policy constraint survives here, while this pre-existing unheld correction retains its status. Reservation-order/fairness work remains separately paused in mp-3ci3.
