---
type: is
id: is-01m0vr1pzhev78rrrvbznbycxz
title: "PR #35 R2 N12: bound LocalBackend exit-waiter retention"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-25T05:59:46.928Z
updated_at: 2026-08-25T06:46:53.708Z
closed_at: 2026-08-25T06:46:53.708Z
close_reason: "Fixed in 4855c8d; exact-head pre-push make verify passed with 4,351 tests and 8 skips, and the per-finding disposition is published at PR #35 comment 5406589771."
resolution: null
duplicate_of: null
---
LocalBackend retains exit waiters at the faster poll cadence. Ensure completed waiter entries are removed or otherwise bounded, with a repeated-launch regression.
