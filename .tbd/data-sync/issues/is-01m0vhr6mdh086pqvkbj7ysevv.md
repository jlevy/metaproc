---
type: is
id: is-01m0vhr6mdh086pqvkbj7ysevv
title: "PR #35 N3: bound descendant observation history and kill work"
kind: bug
status: open
priority: 1
version: 2
labels: []
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0v08wy0cem0nwa7zeejr8qd
created_at: 2026-08-25T04:09:43.820Z
updated_at: 2026-08-25T04:10:15.999Z
---
Prune _observed_descendants and stop re-walking unbounded history at every kill poll. Prove a long-running agent does bounded process lookups and still kills owned descendants.
