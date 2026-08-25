---
type: is
id: is-01m0t4v9e0tas8gh2t745exy3z
title: "M0 vertical slice: offline three-item mapped composite"
kind: task
status: open
priority: 0
version: 8
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T15:04:59.071Z
updated_at: 2026-08-25T19:18:55.606Z
closed_at: null
close_reason: null
resolution: null
duplicate_of: null
---
Run the minimum in-process composite for_each path as a deterministic three-item child process through existing run_fan_out machinery and explicit output declarations. Prove one parent run, no child Metaproc CLI or lease, isolated item failure, failed-item-only child resume, and current plan/status/trace visibility before using provider credentials.

## Notes

The provider-free integration exposed dependency, fingerprint, immutable-input, and adapter-preflight defects. The final offline vertical-slice gate now depends on mp-nxs9. Rerun from the clean consolidated Metaproc head and close only after framework verification and the private downstream exact-pin check both pass.
