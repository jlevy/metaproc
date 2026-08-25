---
type: is
id: is-01m0vr1n0xgcmd6aq695xc0dj7
title: "PR #35 R2 F3: keep normal error shutdown grace"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-25T05:59:44.925Z
updated_at: 2026-08-25T06:46:53.681Z
closed_at: 2026-08-25T06:46:53.681Z
close_reason: "Fixed in 4855c8d; exact-head pre-push make verify passed with 4,351 tests and 8 skips, and the per-finding disposition is published at PR #35 comment 5406589771."
resolution: null
duplicate_of: null
---
RunPool.__aexit__ forces timeout_s=0 for every body exception, immediately terminating in-flight work after an ordinary application error. Reserve zero-grace shutdown for cancellation/explicit kill or preserve the configured grace for normal errors; cover both paths.
