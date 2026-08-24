---
type: is
id: is-01m0t7znk14cmtw76xca444wgq
title: "PR #33 review R2: define or wire real Ctrl-C cancellation"
kind: bug
status: closed
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:48.320Z
updated_at: 2026-08-24T17:54:41.676Z
closed_at: 2026-08-24T17:54:41.676Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. RunExecutionContext.cancellation_event is reached by programmatic task.cancel() but the installed subprocess reaper preempts asyncio SIGINT, so real Ctrl-C never sets it. Wire cooperative SIGINT or explicitly narrow the contract until PR #35 owns the behavior; add real-signal coverage before claiming operator cancellation.
