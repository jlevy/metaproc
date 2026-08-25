---
type: is
id: is-01m0vhr77daaz65d7aeb0jd9n7
title: "PR #35 N4: fence descendants created just before leader exit"
kind: bug
status: open
priority: 1
version: 2
labels: []
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0v08wy0cem0nwa7zeejr8qd
created_at: 2026-08-25T04:09:44.429Z
updated_at: 2026-08-25T04:10:15.999Z
---
Prevent descendants spawned in the final leader-exit window from escaping pool cleanup. Use process-group identity plus create-time ownership or an equally safe existing primitive; add the late-descendant injected-failure regression.
