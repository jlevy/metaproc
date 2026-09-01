---
type: is
id: is-01m1f8y8eqaxq2jn1vv6egw587
title: "PR #59 review R59-1: keep TaskAttemptRecord/0.1 rollback-readable"
kind: bug
status: closed
priority: 0
version: 5
spec_path: src/metaproc/docs/execution-model-design.md
labels:
  - release-blocker
dependencies:
  - type: blocks
    target: is-01m1f8xgv4zycvj17qc7g9x1d0
parent_id: is-01m1f8xgv4zycvj17qc7g9x1d0
created_at: 2026-09-01T20:00:33.750Z
updated_at: 2026-09-01T20:20:43.705Z
closed_at: 2026-09-01T20:20:43.704Z
close_reason: "Metaproc PR #64 merged as 10f51859c6b09ca41cddb9384c7ee0f549de984f after the full local gate and all hosted CI passed; disposition comments were posted on PRs #59 and #60."
resolution: null
duplicate_of: null
---
PR #59 emits anomalies: [] into every strict TaskAttemptRecord/0.1. The prior reader rejects the unknown field, so a rollback cannot read or resume even clean new runs. Preserve the released 0.1 attempt shape, persist accepted anomalies in a separately versioned attempt-owned artifact, test exact serialized keys and read semantics, and update the execution-model and artifact catalog contracts. Related broader compatibility bead: mp-g315.

## Notes

Implemented on codex/downstream-safe-latest. TaskAttemptRecord/0.1 no longer serializes the PR #59 anomalies field. Accepted anomalies use strict metaproc:TaskAttemptAnomalies/0.1 evidence at accepted-anomalies.yaml and project through TaskAttemptRecord.anomalies in memory. Tests cover exact old payload shape, clean absence, sidecar projection, interrupted publication, mismatched identity, contradictory disposition, schema resolution, and symlink containment. Focused suite: 130 passed. Full make verify: 4,578 passed, 8 skipped; lint, typecheck, browser checks, audits, build, and installed-wheel smoke all passed.
