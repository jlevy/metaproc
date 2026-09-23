---
type: is
id: is-01m0sbfqrer7jycjeb8sg470a1
title: "PR #30 review R6: Require one shared Logging client for log fetches"
kind: bug
status: closed
priority: 3
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0sbeq4f6ac3ayz46z7kc03h
created_at: 2026-08-24T07:41:46.126Z
updated_at: 2026-08-24T08:11:43.587Z
closed_at: 2026-08-24T08:11:43.587Z
close_reason: "Fixed in 1cbe8e8; Metaproc PR #30 canonical five-job CI is green."
resolution: null
duplicate_of: null
---
PR #30 review R6 (Low), src/metaproc/cloud/gcp/gcp_run_logs.py:106,118. Remove the dead client fallback that can recreate one-client-per-poll leakage. Review: https://github.com/jlevy/metaproc/pull/30#issuecomment-5392026692
