---
type: is
id: is-01m2kj440fg4pwnjpg5qjkzv1s
title: "PR #83 review P1-R6: rollup Retries column counts failures, not retries"
kind: bug
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:42.542Z
updated_at: 2026-09-15T22:13:42.542Z
---
PR #83 part 1 R6 (Low). operations_rollup.py:141-148 sums every non-succeeded attempt; operations_render.py:65.
