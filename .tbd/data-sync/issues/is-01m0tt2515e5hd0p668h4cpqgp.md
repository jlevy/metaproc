---
type: is
id: is-01m0tt2515e5hd0p668h4cpqgp
title: Preserve required diamond edges beside finished collectors
kind: bug
status: closed
priority: 1
version: 7
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T21:15:44.037Z
updated_at: 2026-08-25T17:01:13.852Z
closed_at: 2026-08-25T17:01:13.851Z
close_reason: Framework-owned graph regressions and full verification prove edge-local failure propagation for strict and tolerant diamond edges. The clean replacement head must preserve this behavior before merge.
resolution: null
duplicate_of: null
---
A process with both a required artifact or reference edge and a collect edge using require: finished can be allowed through when the required producer fails, because ancestry of the collected step masks the separate strict edge. Reproduce with a framework-owned diamond graph, make failure propagation edge-local, and prove the finished collector still runs for failures reachable only through its tolerant collected edge.

## Notes

A pure graph regression reproduces the masking defect. The implementation requires every affected direct dependency to be a finished collector while preserving mixed-outcome replay behavior. Focused and full verification passed on the prior integration head; the replacement must rerun the same framework-owned coverage.
