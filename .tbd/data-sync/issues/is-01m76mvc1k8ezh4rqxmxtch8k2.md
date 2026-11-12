---
type: is
id: is-01m76mvc1k8ezh4rqxmxtch8k2
title: "PR #121 A13: test asserts a Literal accepts its own members"
kind: bug
status: closed
priority: 3
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76msm7xe6a3n7dcegpafnrx
hold: null
hold_until: null
created_at: 2026-11-12T01:09:43.090Z
updated_at: 2026-11-12T01:09:44.042Z
started_at: 2026-11-12T01:09:43.696Z
closed_at: 2026-11-12T01:09:44.041Z
close_reason: "Fixed in 59397b6: test_every_documented_reuse_policy_is_accepted is dropped. Loading exact_inputs is now asserted inside test_exact_inputs_loads_but_planning_refuses_it, and the run-level tests use validated_outputs and never."
resolution: null
duplicate_of: null
---
Low (suggestion). tests/test_reuse_policy.py:296-300.

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
