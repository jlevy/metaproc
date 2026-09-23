---
type: is
id: is-01m2kj430hrv0w498qhmb11zf2
title: "PR #83 review P1-R3: a scope whose plan cannot be read is dropped and labelled a copy"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:41.520Z
updated_at: 2026-09-16T00:17:02.267Z
closed_at: 2026-09-16T00:17:02.266Z
close_reason: "Fixed in 381f378: an unreadable plan keeps its scope and is listed in steps.unreadable_plans; an absent plan still counts as a copy. Tests: test_a_scope_whose_plan_cannot_be_read_is_kept_and_named, test_an_unreadable_root_plan_keeps_the_scopes_of_a_run_nested_in_a_parent."
resolution: null
duplicate_of: null
---
PR #83 part 1 R3 (Medium). scope_path None fails the copied-tree prefix guard, so an unreadable run-plan.yaml scope is counted in ignored_state_dirs and nothing reaches unavailable. engine/operations_summary.py:268-282, engine/operations_render.py:216-220.
