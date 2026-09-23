---
type: is
id: is-01m0xrp05002mxne8snhgsegsa
title: "PR #44 review F10: restore comment-to-code locality"
kind: bug
status: closed
priority: 3
version: 3
labels: []
dependencies: []
parent_id: is-01m0xrncxsdm7q87ywsha5n5x1
created_at: 2026-08-26T00:49:20.543Z
updated_at: 2026-08-26T01:03:37.309Z
closed_at: 2026-08-26T01:03:37.309Z
close_reason: Fixed in b72e3fd; exact-head CI run 32917376865 passed all five jobs.
resolution: null
duplicate_of: null
---
PR #44 F10 (Low). src/metaproc/cloud/gcp/orchestrator_dispatch.py:270-274. Move secret attachment above the Batch job comment block.
