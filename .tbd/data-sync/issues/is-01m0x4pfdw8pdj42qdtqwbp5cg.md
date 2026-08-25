---
type: is
id: is-01m0x4pfdw8pdj42qdtqwbp5cg
title: "R10: release slot ownership when adapter resolution fails"
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T19:00:04.668Z
updated_at: 2026-08-25T19:25:27.663Z
closed_at: 2026-08-25T19:25:27.663Z
close_reason: Fixed by moving adapter resolution inside unconditional teardown and proving ownership release.
resolution: null
duplicate_of: null
---
SlotCoordinator.teardown resolves the adapter before entering its cleanup try/finally. A missing or faulty plugin can therefore strand the slot directory, active counter, and Vehicle-B label lock. Put adapter resolution inside the cleanup boundary and add a regression that forces resolution failure and proves ownership is released.

## Notes

Fixed: adapter resolution is inside teardown's unconditional cleanup boundary; the forced-resolution-failure regression proves ownership release.
