---
type: is
id: is-01m2kj471t970zspanb1g3fvyx
title: "PR #83 review P2-R3: lane equality omits host_max_concurrency"
kind: bug
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:45.658Z
updated_at: 2026-09-15T22:13:45.658Z
---
PR #83 part 2 R3 (Low). RunPoolResources compares three fields; lanes can resolve different host slot ranges. run_process.py:243-266, 2535-2551.
