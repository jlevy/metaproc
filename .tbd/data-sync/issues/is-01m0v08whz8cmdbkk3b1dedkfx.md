---
type: is
id: is-01m0v08whz8cmdbkk3b1dedkfx
title: "PR #34 I10: durable auth_skipped record for adapter mismatch"
kind: task
status: closed
priority: 2
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:16.189Z
updated_at: 2026-08-25T19:28:30.034Z
closed_at: 2026-08-25T05:50:38.955Z
close_reason: null
resolution: null
duplicate_of: null
---
The adapter-mismatch ambient fallback now warns on stderr only — no log record or runpool event, so 'did this run use the pool' is still not machine-answerable (the M1 pool-label assertion needs an artifact); run-parallel worker leg and the gcp-worker fan-out branch remain fully silent. Emit auth_skipped (event + log.warning) on all three sites. Review: pull/34 comment (Finding 2 partial, F5); holistic ledger #10.
