---
type: is
id: is-01m2khvhp06z3zsp1b1agx9x78
title: "PR #79 review R10: remove the unreachable composite branch of _read_step_failure_error"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2khtp39hxxwknrtk7gzqpfs
created_at: 2026-09-15T22:09:01.631Z
updated_at: 2026-09-15T22:56:31.973Z
closed_at: 2026-09-15T22:56:31.969Z
close_reason: "Fixed in eb0b700: removed unreachable branch; scalar composite child errors covered by test_command_diagnostic_reaches_attempt_composite_step_and_root_failure[mapped=False]."
resolution: null
duplicate_of: null
---
PR #79 (https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379), Low. run_process.py:1231-1236: _execute_composite_step returns True or raises CLIError and mapped composites take the fan_out branch, so no caller reaches the scalar composite branch; it also ignores the child's root error. Fix: remove the branch or add a test that reaches it and read the child root error.
