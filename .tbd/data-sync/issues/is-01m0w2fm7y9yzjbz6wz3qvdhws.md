---
type: is
id: is-01m0w2fm7y9yzjbz6wz3qvdhws
title: Make RunPool goldens hermetic in clean Linux full-suite CI
kind: bug
status: closed
priority: 0
version: 8
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - testing
  - ci
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T09:02:08.637Z
updated_at: 2026-08-25T17:01:58.376Z
closed_at: 2026-08-25T17:01:58.375Z
close_reason: RunPool lifecycle goldens are hermetic across full-suite timing while dedicated adaptive-controller coverage remains intact. Repeated focused coverage and full make verify pass; private downstream CI evidence is maintained outside this repository.
resolution: null
duplicate_of: null
---
RunPool lifecycle goldens can pass in isolation on one platform and fail under clean Linux full-suite execution when a fast pressure poll emits valid adaptive events into the snapshot. Replace timing-sensitive fixture behavior with a hermetic pressure interval, retain dedicated controller coverage, prove the snapshots are not blindly regenerated, and run focused repetitions plus full verification.

## Notes

The lifecycle-golden fixture now uses a slow pressure interval while dedicated adaptive-controller tests retain fast polling. Repeated focused coverage and full make verify pass. Private downstream CI evidence is maintained outside this repository.
