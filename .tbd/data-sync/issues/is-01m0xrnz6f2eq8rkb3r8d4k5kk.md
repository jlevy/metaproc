---
type: is
id: is-01m0xrnz6f2eq8rkb3r8d4k5kk
title: "PR #44 review F7: record Secret Manager client close failures"
kind: bug
status: closed
priority: 3
version: 3
labels: []
dependencies: []
parent_id: is-01m0xrncxsdm7q87ywsha5n5x1
created_at: 2026-08-26T00:49:19.566Z
updated_at: 2026-08-26T01:03:37.291Z
closed_at: 2026-08-26T01:03:37.291Z
close_reason: Fixed in b72e3fd; exact-head CI run 32917376865 passed all five jobs.
resolution: null
duplicate_of: null
---
PR #44 F7 (Low). src/metaproc/cloud/gcp/secret_hydration.py:92-96. Do not silently swallow cleanup failure; keep it from masking successful hydration but leave a debug trace.
