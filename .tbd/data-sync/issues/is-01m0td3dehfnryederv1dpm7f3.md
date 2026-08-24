---
type: is
id: is-01m0td3dehfnryederv1dpm7f3
title: "PR #35 self-review: RunPool must own queued submissions during shutdown"
kind: bug
status: closed
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T17:29:13.936Z
updated_at: 2026-08-24T17:54:41.739Z
closed_at: 2026-08-24T17:54:41.739Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
RunPool tracks active launches but drops the asyncio tasks created for submissions. A task waiting on quota, the adaptive semaphore, host admission, or backend launch can survive shutdown and launch work after the pool closes. Track outstanding submission tasks, cancel/drain them on forced shutdown, release acquired admission/semaphores on cancellation, and cover an actually queued sibling.
