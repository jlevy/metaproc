---
type: is
id: is-01m0r93k0sfy1ye28jj2f7db1z
title: Use existing RunPool as the shared mapped-leaf authority
kind: feature
status: open
priority: 0
version: 9
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93mr72xw9k0p8tn94a07d
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
child_order_hints:
  - is-01m0rbq50a74x57jtyp35zrkwh
  - is-01m0t7zqm3kx2kkj4m1hpnfvk4
  - is-01m0vhr666djwntwb8bsb39kgh
created_at: 2026-08-23T21:40:56.472Z
updated_at: 2026-08-25T04:10:37.571Z
---
Make the existing RunPool, owned by RunExecutionContext, govern local resource-bearing leaves across mapped scopes in the initial single-profile topology. Reuse adaptive memory pressure, process-tree supervision, status/events, provider ceilings, and HostAdmissionGate; do not build a second controller. Child mp-g2r0 owns the immediate integration and tests. Add weighted host claims only if a named M3/M4 or concurrent-run measurement proves the current conservative estimate plus host gate cannot meet safety or utilization goals.
