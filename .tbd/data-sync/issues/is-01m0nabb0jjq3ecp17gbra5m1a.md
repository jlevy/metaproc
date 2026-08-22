---
type: is
id: is-01m0nabb0jjq3ecp17gbra5m1a
title: "PR #25 review R6: scalar-path tests for cap exhaustion and repair firing"
kind: task
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0naatygv870nyw2fvaxje15
created_at: 2026-08-22T18:04:55.698Z
updated_at: 2026-08-22T18:23:17.818Z
closed_at: 2026-08-22T18:23:17.818Z
close_reason: "Fixed in a0c8a0a: cap-exhaustion + repair-in-place tests, TestRepairDeclaredOutputs unit tests; c5baaaf added transient tests"
---
Add TestNonFanOutContentRetry cases: retries stop at cap (max_retries:1 -> 2 calls, failed, attempt 2); repair saves attempt 1 (broken-but-repairable frontmatter + format: frontmatter-md -> completed with 1 call).
