---
type: is
id: is-01m2kj471t970zspanb1g3fvyx
title: "PR #83 review P2-R3: lane equality omits host_max_concurrency"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:45.658Z
updated_at: 2026-09-16T00:17:12.063Z
closed_at: 2026-09-16T00:17:12.060Z
close_reason: "Fixed in 2132a32: RunPoolResources compares the resolved host_max_concurrency. Test: test_run_pool_owner_refuses_a_profile_with_different_resources[host_max_concurrency]."
resolution: null
duplicate_of: null
---
PR #83 part 2 R3 (Low). RunPoolResources compares three fields; lanes can resolve different host slot ranges. run_process.py:243-266, 2535-2551.
