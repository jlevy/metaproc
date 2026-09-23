---
type: is
id: is-01m0typxgv83d9wvyrswyn9bzp
title: "PR #38 review 6: make run-parallel backend guard error truthful"
kind: bug
status: closed
priority: 3
version: 3
labels: []
dependencies: []
parent_id: is-01m0typ5swnqc9v7gee2ymkjs9
created_at: 2026-08-24T22:36:58.778Z
updated_at: 2026-08-24T22:56:48.537Z
closed_at: 2026-08-24T22:56:48.537Z
close_reason: Fixed in 809fccc; exact-head CI run 32786763844 passed all five jobs and disposition published at issuecomment-5402607487.
resolution: null
duplicate_of: null
---
Review finding 6 at issuecomment-5402359572. src/metaproc/commands/run_parallel.py:664 calls the gcp-worker admission guard without cloud=, producing guidance for a --cloud flag run-parallel does not expose. Give each caller an actionable command-specific message and test it.
