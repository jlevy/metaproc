---
type: is
id: is-01m0vhr666djwntwb8bsb39kgh
title: Route mapped local leaves through one run-owned RunPool
kind: feature
status: closed
priority: 0
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0r93k0sfy1ye28jj2f7db1z
created_at: 2026-08-25T04:09:43.365Z
updated_at: 2026-08-25T13:19:02.775Z
closed_at: 2026-08-25T13:19:02.775Z
close_reason: The pinned GTIA L0 and live one-item parent run proved one run-owned shared RunPool with no child CLI/lease and proved the required dependency-diamond behavior; framework regressions and full verification pass at b5c4721.
resolution: null
duplicate_of: null
---
Use the existing adaptive RunPool as the resource authority for production-shaped mapped leaves. RunExecutionContext owns the pool for the first single-profile topology; scalar mapped agents submit prepared launches to it, mapped scopes hold no slot, and long-lived command subprocesses use the same path when contract-compatible. Reuse current pressure telemetry, process-tree supervision, status/events, and HostAdmissionGate. Prove no direct scalar launch in the GTIA path. Do not add weighted byte claims unless a named M3/M4 or concurrent-run test fires the documented escalation trigger.
