---
type: is
id: is-01m0vr1mjebqe896dg6m04xbhg
title: "PR #35 R2 N8: retain hard process-tree backstop on repeated SIGINT"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-25T05:59:44.462Z
updated_at: 2026-08-25T06:46:53.675Z
closed_at: 2026-08-25T06:46:53.675Z
close_reason: "Fixed in 4855c8d; exact-head pre-push make verify passed with 4,351 tests and 8 skips, and the per-finding disposition is published at PR #35 comment 5406589771."
resolution: null
duplicate_of: null
---
Cooperative SIGINT leaves CPython second-SIGINT KeyboardInterrupt without the prior hard descendant reaper. Define and test a bounded repeated-signal backstop without bypassing normal cancellation cleanup; assert the expected handler is active at startup.
