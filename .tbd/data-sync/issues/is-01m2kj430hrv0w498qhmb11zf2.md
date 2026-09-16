---
type: is
id: is-01m2kj430hrv0w498qhmb11zf2
title: "PR #83 review P1-R3: a scope whose plan cannot be read is dropped and labelled a copy"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:41.520Z
updated_at: 2026-09-15T22:13:41.520Z
---
PR #83 part 1 R3 (Medium). scope_path None fails the copied-tree prefix guard, so an unreadable run-plan.yaml scope is counted in ignored_state_dirs and nothing reaches unavailable. engine/operations_summary.py:268-282, engine/operations_render.py:216-220.
