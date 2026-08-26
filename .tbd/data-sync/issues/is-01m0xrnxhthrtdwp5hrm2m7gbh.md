---
type: is
id: is-01m0xrnxhthrtdwp5hrm2m7gbh
title: "PR #44 review F2: reject plaintext registered credentials without bound refs"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m0xrncxsdm7q87ywsha5n5x1
created_at: 2026-08-26T00:49:17.881Z
updated_at: 2026-08-26T01:03:37.247Z
closed_at: 2026-08-26T01:03:37.247Z
close_reason: Fixed in b72e3fd; exact-head CI run 32917376865 passed all five jobs.
resolution: null
duplicate_of: null
---
PR #44 F2 (Medium). src/metaproc/commands/gcp_run.py:350-355 and worker dispatch bootstrap env. Reject registered plaintext credential targets independent of whether a Secret Manager ref resolves.
