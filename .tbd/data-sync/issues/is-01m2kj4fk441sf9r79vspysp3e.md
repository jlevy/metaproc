---
type: is
id: is-01m2kj4fk441sf9r79vspysp3e
title: Write an operations summary for each batch child run
kind: feature
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:54.403Z
updated_at: 2026-09-15T22:13:54.403Z
---
PR #83 part 1 S1 (deferred). Finalization writes operations-summary.md only at the run root, so the child runs of a batch have none at execution time, and 'operations summary' on a child computes tokens, cost, RSS and pool figures as null because they live only at the batch root. To make 'operations rollup' across batch children useful, either write a per-child summary when a composite item scope that is a child run completes, or let a child summary read its own slice of the parent's resources.json hierarchy (which already carries per-node metrics). Needs a design decision on which scope counts as a run and on write-once semantics for a child re-run. engine/operations_summary.py _Evidence.load, commands/run_process.py finalization hook.
