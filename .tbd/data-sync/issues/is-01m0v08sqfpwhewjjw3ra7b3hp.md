---
type: is
id: is-01m0v08sqfpwhewjjw3ra7b3hp
title: "PR #37 I2: terminal state on any exception; bound mapped-scope concurrency"
kind: bug
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:13.294Z
updated_at: 2026-08-25T19:28:30.034Z
closed_at: 2026-08-25T13:19:02.272Z
close_reason: "Re-verified addressed at PR #37 head b5c4721: scope identity/containment and exception/concurrency fixes are present, required-edge graph work is split into the base, and the branch descends from post-#38 main without restoring workstation aliasing. Full and pre-push verification passed."
resolution: null
duplicate_of: null
---
Undraft blocker. The mapped invoker catches only CancelledError/CLIError and run_fan_out gathers without return_exceptions, so a bare ValueError/OSError in one item abandons siblings mid-write with status stuck running; and max_concurrency=None means a 500-item roster opens 500 scopes/FDs (EMFILE at default macOS ulimit feeds the same abort). Fix together: catch Exception → mark_failed_at in _invoke, return_exceptions or wrapped invoker, bounded scope default. Review: pull/37 comment (B2+B3); holistic ledger #2.

## Notes

Re-verified OPEN at #37 head 49064f0: item_runner.py:120 gathers without return_exceptions; mapped invoker still catches only CancelledError/CLIError.
