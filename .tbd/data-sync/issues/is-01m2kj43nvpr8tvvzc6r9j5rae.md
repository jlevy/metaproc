---
type: is
id: is-01m2kj43nvpr8tvvzc6r9j5rae
title: "PR #83 review P1-R5: signal-killed run never receives an operations summary"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:42.202Z
updated_at: 2026-09-15T22:13:42.202Z
---
PR #83 part 1 R5 (Medium). SIGTERM handler re-raises the default signal so the finally block never writes operations-summary.md, and status _recover_resource_artifacts does not write it. commands/run_process.py:5651-5675, runpool/kill.py:310-324, commands/status.py:105-131.
