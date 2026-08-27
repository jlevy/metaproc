---
type: is
id: is-01m0rm18kbm24khxjemevb1ybv
title: Project mapped scopes and artifacts through existing views
kind: feature
status: in_progress
priority: 1
version: 13
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93mr72xw9k0p8tn94a07d
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
child_order_hints:
  - is-01m0zs1svbsptksz66728wzdrb
  - is-01m0zswfrt6dcn0qm5t0yftfjd
  - is-01m10mm4vpgbqgrjqx4dbjee41
  - is-01m10s8ad5wcge6npv5p9g370m
created_at: 2026-08-24T00:51:54.602Z
updated_at: 2026-08-27T04:57:04.164Z
---
Extend plan, status, trace, pool rollup, and Metabrowser over mapped source scopes, runtime item instances, child failures, declared outputs, and accepted task results. Add a rebuildable artifact-lineage projection only if these existing views cannot answer the required operator questions.

## Notes

Implemented the minimal generic read-only projection in the working tree. Public API: scan_task_output_projection(run_dir: Path, bundle: PlanBundle) -> TaskOutputProjection. It reuses TaskKey, StatusRecord, TaskAttemptRecord, ResultRecord, state readers and identity validation, recursive composite-scope discovery, PlanBundle, IOSpec, and VizModel; it persists no new state. Accepted outputs require a terminal successful status plus a validated terminal successful result, join by output name to the resolved IOSpec, and safely rebase from the immutable run-config run_dir into a hydrated local root. Runtime records, recursive scope and task directories, and rebased outputs reject or ignore containment and symlink escapes as appropriate. Coverage includes scalar and mapped root tasks, scalar and mapped recursive scopes, failed tasks without results, declaration joins, hydration rebasing, and state, scope, and output symlink safety. Validation: focused projection and visualization suite 61 passed; focused Ruff and BasedPyright clean; repository-wide make verify exited 0; git diff --check clean. Changes remain uncommitted for integration review. No durable ledger, scheduler graph, artifact registry, lineage authority, or consumer-specific fields were added.
