---
type: is
id: is-01m0xrnx7788xxx6gpyfhgab4n
title: "PR #44 review F1: make hydration failures actionable"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m0xrncxsdm7q87ywsha5n5x1
created_at: 2026-08-26T00:49:17.542Z
updated_at: 2026-08-26T01:03:37.206Z
closed_at: 2026-08-26T01:03:37.205Z
close_reason: Fixed in b72e3fd; exact-head CI run 32917376865 passed all five jobs.
resolution: null
duplicate_of: null
---
PR #44 F1 (High). src/metaproc/cloud/gcp/secret_hydration.py:80-91 and three entrypoints. Preserve provider message suppression but surface safe exception classification and actionable missing-extra guidance; ensure entrypoint logs retain diagnostic context.
