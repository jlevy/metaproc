---
type: is
id: is-01m0typy01dp326darcaznyvm0
title: "PR #38 review 7: limit Filestore RUNS_DIR redirection to the GCP worker"
kind: bug
status: closed
priority: 3
version: 3
labels: []
dependencies: []
parent_id: is-01m0typ5swnqc9v7gee2ymkjs9
created_at: 2026-08-24T22:36:59.265Z
updated_at: 2026-08-24T22:56:48.546Z
closed_at: 2026-08-24T22:56:48.546Z
close_reason: Fixed in 809fccc; exact-head CI run 32786763844 passed all five jobs and disposition published at issuecomment-5402607487.
resolution: null
duplicate_of: null
---
Review finding 7 at issuecomment-5402359572. src/metaproc/commands/run_parallel.py:1152 and run_process.py:2171 redirect RUNS_DIR for any non-local backend when Filestore is configured, preserving a split-tree producer for mock/plugin backends after repair paths were removed. Gate the redirection on gcp-worker or justify it with tests.
