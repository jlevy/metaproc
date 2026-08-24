---
type: is
id: is-01m0t803hh1fxvg281pqt5ceay
title: "PR #35 review C1: cover real backend cancellation seams"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T16:00:02.609Z
updated_at: 2026-08-24T16:00:02.609Z
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. Add deterministic real-backend/RunPool coverage for descendant cleanup after normal exit, kill failure during shutdown and monitor accounting, cancel-mid-fan-out, handler cancellation, and real SIGINT. Reuse focused cases rather than a broad fragile scenario.
