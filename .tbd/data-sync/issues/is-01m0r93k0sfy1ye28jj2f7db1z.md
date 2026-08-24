---
type: is
id: is-01m0r93k0sfy1ye28jj2f7db1z
title: Make scalar leaf admission adaptive and shared
kind: feature
status: open
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93mr72xw9k0p8tn94a07d
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
child_order_hints:
  - is-01m0rbq50a74x57jtyp35zrkwh
created_at: 2026-08-23T21:40:56.472Z
updated_at: 2026-08-24T00:51:40.464Z
---
Extend the existing host-admission lease and execution-profile resource hints so scalar agent leaves across mapped scopes admit against current headroom, reserve, active claims, and operator ceilings; record the child process tree; add required posture for new workflows; and preserve the released best-effort default for legacy runs.
