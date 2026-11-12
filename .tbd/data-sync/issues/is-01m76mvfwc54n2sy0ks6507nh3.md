---
type: is
id: is-01m76mvfwc54n2sy0ks6507nh3
title: "PR #121 B3: exact_inputs handler digest is the file on disk, not the code that ran"
kind: bug
status: closed
priority: 1
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76msm7xe6a3n7dcegpafnrx
hold: null
hold_until: null
created_at: 2026-11-12T01:09:47.019Z
updated_at: 2026-11-12T01:09:48.140Z
started_at: 2026-11-12T01:09:47.675Z
closed_at: 2026-11-12T01:09:48.139Z
close_reason: "Fixed by removal in 59397b6: the exact_inputs fingerprinting implementation (ResolvedStep.exact_input_files, locate_handler_source, the fingerprint_step additions, the discovery change, the viz field) is gone, and planning refuses exact_inputs (validate_reuse_policies), so this path cannot be reached. Confirmed by test_exact_inputs_loads_but_planning_refuses_it and test_run_process_refuses_exact_inputs_before_the_run_starts (fail on dee3914, pass after) and by the fingerprint pins, which pass on main's src and on the new head. The future design is tracked as mp-gg7r."
resolution: null
duplicate_of: null
---
High. src/metaproc/engine/code_handler.py:45-53, :115-136, :188-190; src/metaproc/engine/dep_state.py:159-179; src/metaproc/commands/run_process.py:2393, :2427, :3972-3979.

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5867092636
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
