---
type: is
id: is-01m23c19ehv210xqw508hktqt2
title: Subdivide invalid_output in FailureCounts by kind
kind: feature
status: open
priority: 3
version: 2
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels:
  - contract-failure
dependencies: []
parent_id: is-01m269mehrtqg1chz8dqsfzhwr
created_at: 2026-09-09T15:19:27.441Z
updated_at: 2026-09-10T18:47:05.934Z
---
Phase 2 remnant of the contract-failure-primitives spec, verified still open at v0.4.0. src/metaproc/runpool/status.py:95-103 still carries a flat invalid_output: int alongside rate_limited, server_error, timeout, crash and unknown, with no per-kind breakdown and no grouping by label. An operator reading pool status cannot tell a missing output from a malformed one. Was tracked only as a spec checkbox.
