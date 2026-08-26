---
type: is
id: is-01m0xrnyhfhh01ds755ra90by8
title: "PR #44 review F5: normalize dispatch-side validation exception types"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m0xrncxsdm7q87ywsha5n5x1
created_at: 2026-08-26T00:49:18.895Z
updated_at: 2026-08-26T01:03:37.279Z
closed_at: 2026-08-26T01:03:37.279Z
close_reason: Fixed in b72e3fd; exact-head CI run 32917376865 passed all five jobs.
resolution: null
duplicate_of: null
---
PR #44 F5 (Medium). src/metaproc/cloud/gcp/secret_hydration.py:35-45. Make attach_secret_refs consistently raise ValueError for operator-side contract validation.
