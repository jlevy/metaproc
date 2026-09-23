---
type: is
id: is-01m0vr1m3c3ejrtef1t00tzh7t
title: "PR #35 R2 N7: preserve cancellation during filter-thread drain"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-25T05:59:43.980Z
updated_at: 2026-08-25T06:46:53.669Z
closed_at: 2026-08-25T06:46:53.669Z
close_reason: "Fixed in 4855c8d; exact-head pre-push make verify passed with 4,351 tests and 8 skips, and the per-finding disposition is published at PR #35 comment 5406589771."
resolution: null
duplicate_of: null
---
_await_task_completion consumes CancelledError. launch_and_supervise does not re-raise after a filter-thread join, so Ctrl-C in that window can be lost. Preserve the caller cancellation after owned cleanup completes and add a race regression.
