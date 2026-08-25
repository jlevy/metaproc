---
type: is
id: is-01m0t4v9e0tas8gh2t745exy3z
title: "M0 vertical slice: offline three-item mapped composite"
kind: task
status: open
priority: 0
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels: []
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T15:04:59.071Z
updated_at: 2026-08-25T04:10:38.929Z
---
Implement only the minimum in-process composite for_each path needed to run a deterministic three-item child process through the existing run_fan_out machinery with one declared output port. Prove one parent run, no child metaproc CLI or lease, isolated item failure, resume of only the failed item's actionable child chain, and current plan/status/trace visibility before expanding the mapped-scope design.

## Notes

The first provider-free integration exposed dependency, fingerprint, immutable-input, and adapter-preflight defects, but the final L0 is now gated on mp-nxs9. Rerun from a clean published trading integration head pinned to the repaired consolidated PR #37 commit; close only after trading CI executes that exact L0.
