---
type: is
id: is-01m0vhr666djwntwb8bsb39kgh
title: Route mapped local leaves through one run-owned RunPool
kind: feature
status: closed
priority: 0
version: 7
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0r93k0sfy1ye28jj2f7db1z
created_at: 2026-08-25T04:09:43.365Z
updated_at: 2026-08-25T19:18:15.624Z
closed_at: 2026-08-25T17:01:13.596Z
close_reason: Framework-owned regressions and full verification prove that one run-owned RunPool governs mapped leaves without child CLI or nested lease acquisition. The clean replacement head must preserve this behavior before merge.
resolution: null
duplicate_of: null
---
Use the existing adaptive RunPool as the resource authority for production-shaped mapped leaves. RunExecutionContext owns the pool for the first single-profile topology; scalar mapped agents submit prepared launches to it, mapped scopes hold no slot, and long-lived command subprocesses use the same path when contract-compatible. Reuse current pressure telemetry, process-tree supervision, status/events, and HostAdmissionGate. Prove no direct scalar launch in the mapped-scope path. Do not add weighted byte claims without a measured trigger.
