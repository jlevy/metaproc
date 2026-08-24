---
type: is
id: is-01m0t7znk14cmtw76xca444wgq
title: "PR #33 review R2: define or wire real Ctrl-C cancellation"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:48.320Z
updated_at: 2026-08-24T15:59:48.320Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. RunExecutionContext.cancellation_event is reached by programmatic task.cancel() but the installed subprocess reaper preempts asyncio SIGINT, so real Ctrl-C never sets it. Wire cooperative SIGINT or explicitly narrow the contract until PR #35 owns the behavior; add real-signal coverage before claiming operator cancellation.
