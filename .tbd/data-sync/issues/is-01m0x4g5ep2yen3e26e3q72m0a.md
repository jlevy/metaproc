---
type: is
id: is-01m0x4g5ep2yen3e26e3q72m0a
title: "R9: remove released attempt-history claims from Unreleased"
kind: bug
status: closed
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T18:56:37.845Z
updated_at: 2026-08-25T19:25:27.349Z
closed_at: 2026-08-25T19:25:27.348Z
close_reason: Fixed by removing released claims from the Unreleased changelog section.
resolution: null
duplicate_of: null
---
The clean branch starts from the v0.3.0 release, but Unreleased repeats durable attempt history and crash reconciliation that are already in the v0.3.0 section. Remove those stale stack claims so the consolidated PR documents only its delta from released main.

## Notes

Fixed: Unreleased now states only the consolidated branch delta from the v0.3.0 baseline.
