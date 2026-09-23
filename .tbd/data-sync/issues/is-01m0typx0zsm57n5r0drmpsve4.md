---
type: is
id: is-01m0typx0zsm57n5r0drmpsve4
title: "PR #38 review 5: add explicit Batch orchestrator admission signal"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m0typ5swnqc9v7gee2ymkjs9
created_at: 2026-08-24T22:36:58.271Z
updated_at: 2026-08-24T22:56:48.528Z
closed_at: 2026-08-24T22:56:48.528Z
close_reason: Fixed in 809fccc; exact-head CI run 32786763844 passed all five jobs and disposition published at issuecomment-5402607487.
resolution: null
duplicate_of: null
---
Review finding 5 at issuecomment-5402359572. src/metaproc/commands/run_process.py:3494 hard-gates gcp-worker on BATCH_TASK_INDEX even though that variable is external. Add an explicit signal set by orchestrator_dispatch and assert the signal survives container bootstrap.
