---
type: is
id: is-01m2kj43nvpr8tvvzc6r9j5rae
title: "PR #83 review P1-R5: signal-killed run never receives an operations summary"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:42.202Z
updated_at: 2026-09-16T00:17:03.429Z
closed_at: 2026-09-16T00:17:03.427Z
close_reason: "Fixed in 35dcb81: status-triggered recovery writes a missing operations-summary.md with trigger 'status'. Tests: test_inactive_status_writes_a_missing_operations_summary_once, test_active_status_never_writes_an_operations_summary."
resolution: null
duplicate_of: null
---
PR #83 part 1 R5 (Medium). SIGTERM handler re-raises the default signal so the finally block never writes operations-summary.md, and status _recover_resource_artifacts does not write it. commands/run_process.py:5651-5675, runpool/kill.py:310-324, commands/status.py:105-131.
