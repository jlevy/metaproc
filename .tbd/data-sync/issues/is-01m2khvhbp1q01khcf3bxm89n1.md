---
type: is
id: is-01m2khvhbp1q01khcf3bxm89n1
title: "PR #79 review R9: a mapped code item refused before launch must record its own failed status and cause"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m2khtp39hxxwknrtk7gzqpfs
created_at: 2026-09-15T22:09:01.301Z
updated_at: 2026-09-15T22:56:29.633Z
closed_at: 2026-09-15T22:56:29.586Z
close_reason: "Fixed in 77cab31: mapped code items record prelaunch CLIError via mark_failed_synthetic_at (permanent). Test: test_mapped_item_refused_before_launch_records_its_own_failure."
resolution: null
duplicate_of: null
---
PR #79 (https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379), Medium. run_process.py:1587 validate_step_inputs_exist raises CLIError before mark_running_at; run_fan_out (return_exceptions=True) re-raises after siblings finish; _run_one_step_preserving_errors (4390-4436) records it at step level only. No item key, no task status, fan-in collect_item_outcomes reports not_reached with no cause. Fix: catch CLIError in _execute_code_fan_out_step._invoke (1965) and the aligned-chain _invoke, write mark_failed_synthetic_at with the refusal, return False. Regression: two-item case in tests/test_boundary_failure_causes.py.
