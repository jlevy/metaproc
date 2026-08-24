---
type: is
id: is-01m0t803hh1fxvg281pqt5ceay
title: "PR #35 review C1: cover real backend cancellation seams"
kind: bug
status: closed
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T16:00:02.609Z
updated_at: 2026-08-24T17:54:41.714Z
closed_at: 2026-08-24T17:54:41.714Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. Add deterministic real-backend/RunPool coverage for descendant cleanup after normal exit, kill failure during shutdown and monitor accounting, cancel-mid-fan-out, handler cancellation, and real SIGINT. Reuse focused cases rather than a broad fragile scenario.
