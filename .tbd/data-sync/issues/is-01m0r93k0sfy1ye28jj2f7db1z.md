---
type: is
id: is-01m0r93k0sfy1ye28jj2f7db1z
title: Enforce universal harness-aware resource admission
kind: feature
status: open
priority: 1
version: 3
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93mr72xw9k0p8tn94a07d
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
child_order_hints:
  - is-01m0rbq50a74x57jtyp35zrkwh
created_at: 2026-08-23T21:40:56.472Z
updated_at: 2026-08-23T22:26:34.633Z
---
Route every executable leaf through correctly scoped authorities. Admit on current memory headroom and conservative harness/profile claims, charge the full supervised process tree, share host authority across runs, preserve provider namespaces, and never launch ungoverned work when admission is unavailable.
