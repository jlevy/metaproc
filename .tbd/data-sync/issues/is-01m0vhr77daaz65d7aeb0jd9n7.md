---
type: is
id: is-01m0vhr77daaz65d7aeb0jd9n7
title: "PR #35 N4: fence descendants created just before leader exit"
kind: bug
status: closed
priority: 1
version: 4
labels: []
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0v08wy0cem0nwa7zeejr8qd
created_at: 2026-08-25T04:09:44.429Z
updated_at: 2026-08-25T06:46:53.650Z
closed_at: 2026-08-25T06:46:53.650Z
close_reason: "Fixed in 4855c8d; exact-head pre-push make verify passed with 4,351 tests and 8 skips, and the per-finding disposition is published at PR #35 comment 5406589771."
resolution: null
duplicate_of: null
---
Prevent descendants spawned in the final leader-exit window from escaping pool cleanup. Use process-group identity plus create-time ownership or an equally safe existing primitive; add the late-descendant injected-failure regression.
