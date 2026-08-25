---
type: is
id: is-01m0vr1p242fa0sh9569ttbbre
title: "PR #35 R2 N10: fence RunPool health sampling against PID reuse"
kind: bug
status: closed
priority: 3
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-25T05:59:45.988Z
updated_at: 2026-08-25T06:46:53.694Z
closed_at: 2026-08-25T06:46:53.694Z
close_reason: "Fixed in 4855c8d; exact-head pre-push make verify passed with 4,351 tests and 8 skips, and the per-finding disposition is published at PR #35 comment 5406589771."
resolution: null
duplicate_of: null
---
RunPool health reads psutil.Process(pid) RSS without checking the launch identity, so a recycled PID can attribute unrelated memory to a completed handle. Reuse launch identity validation or explicitly defer with bounded impact evidence.
