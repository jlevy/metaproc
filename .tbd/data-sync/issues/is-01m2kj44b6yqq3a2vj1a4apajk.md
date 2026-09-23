---
type: is
id: is-01m2kj44b6yqq3a2vj1a4apajk
title: "PR #83 review P1-R7: retry rows merge same-named steps from different processes"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:42.885Z
updated_at: 2026-09-16T00:17:05.145Z
closed_at: 2026-09-16T00:17:05.143Z
close_reason: "Fixed in 381f378: RetryStepRow keys by process and step id. Test: test_retries_count_attempts_beyond_the_first_and_keep_processes_apart."
resolution: null
duplicate_of: null
---
PR #83 part 1 R7 (Low). retries.by_step keys by step_id only. engine/operations_summary.py:1010.
