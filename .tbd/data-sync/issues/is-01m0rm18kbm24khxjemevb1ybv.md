---
type: is
id: is-01m0rm18kbm24khxjemevb1ybv
title: Project mapped scopes and artifacts through existing views
kind: feature
status: in_progress
priority: 1
version: 8
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93mr72xw9k0p8tn94a07d
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-24T00:51:54.602Z
updated_at: 2026-08-26T19:24:56.094Z
---
Extend plan, status, trace, pool rollup, and Metabrowser over mapped source scopes, runtime item instances, child failures, declared outputs, and accepted task results. Add a rebuildable artifact-lineage projection only if these existing views cannot answer the required operator questions.

## Notes

Implemented the minimal generic read-only projection in the working tree. Public API: . It reuses TaskKey, StatusRecord, TaskAttemptRecord, ResultRecord, state readers/identity validation, recursive composite-scope discovery, PlanBundle, IOSpec, and VizModel; it persists no new state. Accepted outputs require a terminal successful status plus a validated terminal successful result, join by output name to the resolved IOSpec, and safely rebase from the immutable run-config run_dir into a hydrated local root. Runtime records, recursive scope/task directories, and rebased outputs reject or ignore containment and symlink escapes as appropriate. Coverage includes scalar and mapped root tasks, scalar and mapped recursive scopes, failed tasks without results, declaration joins, hydration rebasing, and state/scope/output symlink safety. Validation: focused projection/viz suite 61 passed; focused Ruff and BasedPyright clean; repository-wide make verify exited 0; git diff --check clean. Changes remain uncommitted for integration review. No durable ledger, scheduler graph, artifact registry, lineage authority, or consumer-specific fields were added.
