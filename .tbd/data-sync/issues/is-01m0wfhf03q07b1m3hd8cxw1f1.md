---
type: is
id: is-01m0wfhf03q07b1m3hd8cxw1f1
title: Include run-owned root pools in pool rollup
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - operator-smoke
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T12:50:20.290Z
updated_at: 2026-08-25T13:19:02.512Z
closed_at: 2026-08-25T13:19:02.512Z
close_reason: "Fixed at b5c4721; regressions, full 4385-test gate, pre-push gate, and successful-run operator replay passed. PR #37 has no checks because its stacked base is not main; exact-pin consumer CI remains the independent gate."
resolution: null
duplicate_of: null
---
Fresh GTIA v3.0-pre used one canonical run-owned RunPool and pool status reported 1 completed/0 failed, but pool rollup reported no sub-step pools because it scans only .state/steps. Include each composite scope root .state/runpool-status.yaml and matching root event stream, deduplicated alongside step pools.
