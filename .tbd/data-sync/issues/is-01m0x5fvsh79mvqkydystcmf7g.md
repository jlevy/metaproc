---
type: is
id: is-01m0x5fvsh79mvqkydystcmf7g
title: "R13: terminalize code and mapped attempts on orchestration abort"
kind: bug
status: closed
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T19:13:56.518Z
updated_at: 2026-08-25T19:25:28.824Z
closed_at: 2026-08-25T19:25:28.823Z
close_reason: Fixed with terminal code and mapped-item attempt state for cancellation and nonstandard orchestration aborts.
resolution: null
duplicate_of: null
---
Review finding R13. A cancelled mode:code task drains its synchronous process correctly but bypasses the ordinary Exception handler, leaving its durable attempt record running. A BaseException escaping a mapped child has the same stale-running failure mode. Record cancelled/lost terminal dispositions before propagating orchestration-level aborts; cover both paths with regression tests.

## Notes

Fixed: mode:code cancellation records cancelled after owned synchronous work drains; other BaseException records lost. Mapped child aborts terminalize the parent item before propagation. Focused regressions and full local make verify pass.
