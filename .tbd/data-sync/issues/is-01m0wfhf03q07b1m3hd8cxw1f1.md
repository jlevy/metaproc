---
type: is
id: is-01m0wfhf03q07b1m3hd8cxw1f1
title: Include run-owned root pools in pool rollup
kind: bug
status: closed
priority: 1
version: 4
spec_path: null
labels:
  - operator-smoke
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T12:50:20.290Z
updated_at: 2026-08-25T17:00:19.084Z
closed_at: 2026-08-25T13:19:02.512Z
close_reason: "Fixed at b5c4721; regressions, full 4385-test gate, pre-push gate, and successful-run operator replay passed. PR #37 has no checks because its stacked base is not main; exact-pin consumer CI remains the independent gate."
resolution: null
duplicate_of: null
---
A mapped-scope run can use one canonical run-owned RunPool while pool rollup reports no pools because it scans only step-local state. Include each composite scope root runpool status and event stream, deduplicated alongside step pools.

## Notes

The generic rollup regression and full verification passed on the prior integration head. The clean replacement must preserve root-pool discovery without adding workflow-specific paths.
