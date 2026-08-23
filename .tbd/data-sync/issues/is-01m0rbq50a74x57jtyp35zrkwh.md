---
type: is
id: is-01m0rbq50a74x57jtyp35zrkwh
title: Do not persist pool-capacity waits as execution attempts
kind: bug
status: open
priority: 1
version: 1
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r93k0sfy1ye28jj2f7db1z
created_at: 2026-08-23T22:26:34.633Z
updated_at: 2026-08-23T22:26:34.633Z
---
Pre-commit review finding: _build_prepare_launch writes mark_running before credential-slot admission, and launch/preparation failures synthesize TaskAttemptRecords. The pool-exhausted path explicitly does not consume the production retry budget, while replay counts every lost record against max_attempts, so repeated admission waits can replay as failed even while production keeps waiting. Move task state through admission_wait without an attempt fact; create AttemptStarted only after a launch claim is admitted. Separately encode class-specific quota retry policy so replay and resume enforce the same budget.
