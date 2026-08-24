---
type: is
id: is-01m0r93k0sfy1ye28jj2f7db1z
title: Unify host byte admission across pool and scalar launches
kind: feature
status: open
priority: 1
version: 6
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
updated_at: 2026-08-24T02:23:13.595Z
---
Build one filesystem-backed host authority shared by RunPool and scalar leaves. Under a decision mutex, admit against fresh headroom, reserve, operator count ceiling, and a ledger of active byte claims; attach child identity and observed process-tree footprint; make required posture fail closed; and ensure RunPool ramp and warm-state restoration re-consult the same authority after a fresh pressure sample. Preserve legacy best-effort behavior outside opted-in workflows.
