---
type: is
id: is-01m0sbfscjjzeaq2gxh78rfdz3
title: "PR #30 review S4: Close the Batch client after GCP log tailing"
kind: task
status: closed
priority: 3
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0sbeq4f6ac3ayz46z7kc03h
created_at: 2026-08-24T07:41:47.793Z
updated_at: 2026-08-24T08:11:43.610Z
closed_at: 2026-08-24T08:11:43.610Z
close_reason: "Fixed in 1cbe8e8; Metaproc PR #30 canonical five-job CI is green."
resolution: null
duplicate_of: null
---
PR #30 review S4. Close BatchServiceClient in the same tailing finally path as Logging client where the library surface supports close. Review: https://github.com/jlevy/metaproc/pull/30#issuecomment-5392026692
