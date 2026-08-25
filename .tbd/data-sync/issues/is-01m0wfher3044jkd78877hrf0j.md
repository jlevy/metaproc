---
type: is
id: is-01m0wfher3044jkd78877hrf0j
title: Rebuild status plans with recorded execution identity
kind: bug
status: closed
priority: 0
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - operator-smoke
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T12:50:20.035Z
updated_at: 2026-08-25T13:19:02.503Z
closed_at: 2026-08-25T13:19:02.503Z
close_reason: "Fixed at b5c4721; regressions, full 4385-test gate, pre-push gate, and successful-run operator replay passed. PR #37 has no checks because its stacked base is not main; exact-pin consumer CI remains the independent gate."
resolution: null
duplicate_of: null
---
Fresh GTIA v3.0-pre parent run completed and exact resume skipped all work, but metaproc status projected all completed steps stale. _load_plan_from_run rebuilds without the run-config execution_profile or artifact_namespace, so adapter/profile-sensitive fingerprints differ. Rebuild with the recorded execution identity and add a regression that launches under a non-default profile then reports current.
