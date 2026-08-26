---
type: is
id: is-01m0v08sb5rqd36kqwxytt4wyf
title: "PR #37 I1: give mapped child scopes their own run identity"
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
created_at: 2026-08-24T23:04:12.899Z
updated_at: 2026-08-25T19:28:30.034Z
closed_at: 2026-08-25T13:19:02.256Z
close_reason: "Re-verified addressed at PR #37 head b5c4721: scope identity/containment and exception/concurrency fixes are present, required-edge graph work is split into the base, and the branch descends from post-#38 main without restoring workstation aliasing. Full and pre-push verification passed."
resolution: null
duplicate_of: null
---
Undraft blocker. child_vars never rebinds RUN_ID/RUNS_DIR, so every mapped item's {{run.dir}} resolves to the PARENT run dir: N items mapping a child with any non-item-keyed output path silently overwrite one file and each item's boundary validation passes on whatever another item wrote; a current test pins the broken behavior in. Root cause is structural (three coexisting run-identity notions); fix via one ScopeIdentity (path-relative scope id + scope path + scope dir) derived once per scope and consumed by template resolution, pool binding, node IDs, and events — not another local rebind. Add the two-item fixed-output-path test. Review: https://github.com/jlevy/metaproc/pull/37#issuecomment-5402359194 (B1); holistic ledger #1.

## Notes

Re-verified OPEN at #37 head 49064f0 (2026-08-24): run_process.py:2269 still child_vars = dict(variables), no RUN_ID/RUNS_DIR rebind. Handoff note: https://github.com/jlevy/metaproc/pull/37#issuecomment-5402751366
