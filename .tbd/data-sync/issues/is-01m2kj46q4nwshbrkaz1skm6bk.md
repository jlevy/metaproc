---
type: is
id: is-01m2kj46q4nwshbrkaz1skm6bk
title: "PR #83 review P2-R2: --step-variant on a top-level composite is accepted then ignored"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:45.314Z
updated_at: 2026-09-16T00:17:11.610Z
closed_at: 2026-09-16T00:17:11.605Z
close_reason: "Fixed in 6f999b0: launch validation refuses --step-variant for a composite step, pinned or not. Tests: test_step_variant_naming_a_composite_step_fails_whether_or_not_it_is_pinned, test_run_process_refuses_a_step_variant_for_a_composite_step."
resolution: null
duplicate_of: null
---
PR #83 part 2 R2 (Medium). Unpinned composite override recorded then ignored when planning the child; on a pinned composite it replaces the pin. engine/build_plan.py:111-129, 875-887; commands/run_process.py:522-536.
