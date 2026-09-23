---
type: is
id: is-01m0sbfqb6gmmdbhk4c5n4dkty
title: "PR #30 review R5: Normalize GCP log watermarks to fixed UTC precision"
kind: bug
status: closed
priority: 3
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0sbeq4f6ac3ayz46z7kc03h
created_at: 2026-08-24T07:41:45.702Z
updated_at: 2026-08-24T08:11:43.579Z
closed_at: 2026-08-24T08:11:43.579Z
close_reason: "Fixed in 1cbe8e8; Metaproc PR #30 canonical five-job CI is green."
resolution: null
duplicate_of: null
---
PR #30 review R5 (Low), src/metaproc/cloud/gcp/gcp_run_logs.py:96,229. Emit fixed-microsecond UTC RFC3339 strings so lexicographic watermark comparison preserves time order. Review: https://github.com/jlevy/metaproc/pull/30#issuecomment-5392026692
