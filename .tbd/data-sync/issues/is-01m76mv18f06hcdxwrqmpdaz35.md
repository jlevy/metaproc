---
type: is
id: is-01m76mv18f06hcdxwrqmpdaz35
title: "PR #121 A4: exact_inputs hashes a run-written file unless its path equals a declared output"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76msm7xe6a3n7dcegpafnrx
hold: null
hold_until: null
created_at: 2026-11-12T01:09:32.046Z
updated_at: 2026-11-12T01:09:33.157Z
started_at: 2026-11-12T01:09:32.707Z
closed_at: 2026-11-12T01:09:33.155Z
close_reason: "Fixed by removal in 59397b6: the exact_inputs fingerprinting implementation (ResolvedStep.exact_input_files, locate_handler_source, the fingerprint_step additions, the discovery change, the viz field) is gone, and planning refuses exact_inputs (validate_reuse_policies), so this path cannot be reached. Confirmed by test_exact_inputs_loads_but_planning_refuses_it and test_run_process_refuses_exact_inputs_before_the_run_starts (fail on dee3914, pass after) and by the fingerprint pins, which pass on main's src and on the new head. The future design is tracked as mp-gg7r."
resolution: null
duplicate_of: null
---
Medium. src/metaproc/engine/build_plan.py:772-780, :796-797. A file under an upstream directory output gives plan and result different fingerprints (step-mismatch).

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
