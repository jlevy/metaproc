---
type: is
id: is-01m0rm18kbm24khxjemevb1ybv
title: Project mapped scopes and artifacts through existing views
kind: feature
status: open
priority: 1
version: 6
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93mr72xw9k0p8tn94a07d
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-24T00:51:54.602Z
updated_at: 2026-08-26T19:04:10.268Z
---
Extend plan, status, trace, pool rollup, and Metabrowser over mapped source scopes, runtime item instances, child failures, declared outputs, and accepted task results. Add a rebuildable artifact-lineage projection only if these existing views cannot answer the required operator questions.

## Notes

Read-only architecture review recommends one rebuildable task/output projection over existing records, not a new durable ledger. Reuse TaskKey, StatusRecord, TaskAttemptRecord, ResultRecord, state_io readers and identity validation, iter_composite_run_dirs, PlanBundle, and VizModel. The projection should index task instances and accepted ResultRecord outputs, safely rebase recorded output paths for hydrated runs, and feed status, trace, resource, and Metabrowser views. Repair mapped-child descent in scan_bundle_progress and result-only trace assumptions; do not add an artifact-lineage registry or scheduler graph.
