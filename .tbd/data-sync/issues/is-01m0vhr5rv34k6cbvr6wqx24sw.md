---
type: is
id: is-01m0vhr5rv34k6cbvr6wqx24sw
title: "Restack PRs #32-#37 on post-release main"
kind: task
status: in_progress
priority: 0
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-25T04:09:42.938Z
updated_at: 2026-08-25T04:55:59.689Z
---
Rebase the retained review stack bottom-up onto main at or after 6ac9c65. Preserve coherent PR boundaries, update every child base to the exact repaired parent, and resolve the PR #38 run-config conflict deliberately: retain immutable-variable resume validation and the released no-workstation-alias behavior. Record old and new head mappings and require no semantic conflict to be resolved mechanically.
