---
type: is
id: is-01m0v08sb5rqd36kqwxytt4wyf
title: "PR #37 I1: give mapped child scopes their own run identity"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:12.899Z
updated_at: 2026-08-24T23:04:12.899Z
---
Undraft blocker. child_vars never rebinds RUN_ID/RUNS_DIR, so every mapped item's {{run.dir}} resolves to the PARENT run dir: N items mapping a child with any non-item-keyed output path silently overwrite one file and each item's boundary validation passes on whatever another item wrote; a current test pins the broken behavior in. Root cause is structural (three coexisting run-identity notions); fix via one ScopeIdentity (path-relative scope id + scope path + scope dir) derived once per scope and consumed by template resolution, pool binding, node IDs, and events — not another local rebind. Add the two-item fixed-output-path test. Review: https://github.com/jlevy/metaproc/pull/37#issuecomment-5402359194 (B1); holistic ledger #1.
