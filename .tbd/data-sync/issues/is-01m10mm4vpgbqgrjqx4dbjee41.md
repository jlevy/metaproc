---
type: is
id: is-01m10mm4vpgbqgrjqx4dbjee41
title: Bind hydrated runtime projection to the recorded resolved plan
kind: bug
status: closed
priority: 0
version: 7
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
  - runtime-projection
dependencies: []
parent_id: is-01m0rm18kbm24khxjemevb1ybv
child_order_hints:
  - is-01m10nrk9yd563zz35e2zt4yag
  - is-01m10nrksafes6r6q5e02pk4sx
  - is-01m10nrm81d0rfve9jggxanym6
  - is-01m10qt94wng0j0avjgjbkb3x8
created_at: 2026-08-27T03:36:08.822Z
updated_at: 2026-08-27T04:36:02.260Z
closed_at: 2026-08-27T04:36:02.260Z
close_reason: "Fixed in 9d34c1f; full make verify passed with 4,493 tests and GitHub CI completed 5/5 green. Published per-finding dispositions on PR #49."
resolution: null
duplicate_of: null
---
A real nested mapped run reconstructed by load_plan_bundle_from_run yields step_binding=mismatch for every recorded output. The loader drops recorded variant/profile inputs and recursively reuses the root scope variables instead of each child scope's run.dir and runtime bindings, so scan_task_output_projection rejects all outputs as step-mismatch. Reproduce against a portable nested fixture, then reconstruct the exact recorded resolved plan (or consume a persisted exact plan authority) without introducing a second state model. Gate: hydrated local or relocated run yields exact step bindings and accepted outputs; forged/current spec drift remains rejected.
