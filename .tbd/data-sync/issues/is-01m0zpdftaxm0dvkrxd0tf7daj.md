---
type: is
id: is-01m0zpdftaxm0dvkrxd0tf7daj
title: Code fan-out bare resume re-invokes completed items
kind: bug
status: closed
priority: 1
version: 2
labels:
  - resume
  - fanout
dependencies: []
created_at: 2026-08-26T18:48:13.385Z
updated_at: 2026-09-09T06:29:31.885Z
closed_at: 2026-09-09T06:29:31.885Z
close_reason: "Fixed and pinned. Confirmed still present at df01cb7 on _execute_code_fan_out_step (run_process.py), which the bead's symptom description outlived: the specific case it named, terminal review projections, was fixed on 2026-08-26 by nonterminal_contexts() dropping terminal items. The general claim stayed true for reason in {completed, cached, running}. _discover_chain_items returns nonterminal_contexts() and hands it to run_fan_out, which has no is_done parameter, and _execute_code_step has no reuse guard, so every completed item was re-invoked and recorded a fresh attempt. Regression origin 6a548e9 (2026-08-21), which is an ancestor of v0.3.0, so this shipped and is a genuine release fix rather than an in-cycle regression. Reachable from any mode:code step with for_each that is not in an item-aligned chain; chains require align: same_key and were already guarded. Fix extracts the aligned-chain executor's own _is_done predicate into _item_task_is_done and applies it on both paths, so there is one implementation rather than two. The run-plan roster still receives the full non-terminal set; only execution is filtered. New tests/test_code_fan_out_resume.py with a fixture reaching this executor (the repo had none: replay_smoke routes to the chain path via align: same_key). Both tests fail at HEAD and pass with the fix. Full suite 4,613 passed, 8 skipped."
resolution: null
duplicate_of: null
---
The code fan-out discovery path returns both actionable and filtered completed items, and the executor invokes every row. A bare same-RUN_ID resume therefore creates new attempts for completed code fan-out items such as terminal review projections. Add a focused regression and retain validated completed items unless force or invalidation requires a rerun.
