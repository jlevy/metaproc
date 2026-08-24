---
type: is
id: is-01m0t7zyt479vkhz8fedxgesca
title: "PR #34 review O1: document GCP orchestrator-worker label contention"
kind: bug
status: closed
priority: 3
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3r59m3mpwg54j5s6qhf
created_at: 2026-08-24T15:59:57.764Z
updated_at: 2026-08-24T16:38:02.879Z
closed_at: 2026-08-24T16:38:02.879Z
close_reason: "Fixed in e3f177b; exact-head make verify passed (4,318 passed, 8 skipped) and disposition published on PR #34."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/34#issuecomment-5397585053. A gcp-worker orchestrator running local scalar steps now contends with its workers for the same pool labels. Confirm intent and add a concise operator note if retained.
