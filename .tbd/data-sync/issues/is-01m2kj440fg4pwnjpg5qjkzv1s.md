---
type: is
id: is-01m2kj440fg4pwnjpg5qjkzv1s
title: "PR #83 review P1-R6: rollup Retries column counts failures, not retries"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:42.542Z
updated_at: 2026-09-16T00:17:04.207Z
closed_at: 2026-09-16T00:17:04.206Z
close_reason: "Fixed in 381f378: retries.retries counts attempts beyond each task's first; the rollup shows retries beside not_succeeded. Test: test_retries_count_attempts_beyond_the_first_and_keep_processes_apart."
resolution: null
duplicate_of: null
---
PR #83 part 1 R6 (Low). operations_rollup.py:141-148 sums every non-succeeded attempt; operations_render.py:65.
