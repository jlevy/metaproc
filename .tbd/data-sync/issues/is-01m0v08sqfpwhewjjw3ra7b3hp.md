---
type: is
id: is-01m0v08sqfpwhewjjw3ra7b3hp
title: "PR #37 I2: terminal state on any exception; bound mapped-scope concurrency"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:13.294Z
updated_at: 2026-08-24T23:04:13.294Z
---
Undraft blocker. The mapped invoker catches only CancelledError/CLIError and run_fan_out gathers without return_exceptions, so a bare ValueError/OSError in one item abandons siblings mid-write with status stuck running; and max_concurrency=None means a 500-item roster opens 500 scopes/FDs (EMFILE at default macOS ulimit feeds the same abort). Fix together: catch Exception → mark_failed_at in _invoke, return_exceptions or wrapped invoker, bounded scope default. Review: pull/37 comment (B2+B3); holistic ledger #2.
