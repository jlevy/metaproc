---
type: is
id: is-01m0wfher3044jkd78877hrf0j
title: Rebuild status plans with recorded execution identity
kind: bug
status: closed
priority: 0
version: 4
spec_path: null
labels:
  - operator-smoke
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T12:50:20.035Z
updated_at: 2026-08-25T17:00:18.616Z
closed_at: 2026-08-25T13:19:02.503Z
close_reason: "Fixed at b5c4721; regressions, full 4385-test gate, pre-push gate, and successful-run operator replay passed. PR #37 has no checks because its stacked base is not main; exact-pin consumer CI remains the independent gate."
resolution: null
duplicate_of: null
---
A fresh mapped-scope parent run completed and exact resume skipped all work, but status projected completed steps stale because plan reconstruction omitted recorded execution identity. Rebuild status plans with the run-config execution profile and artifact namespace, then prove a run launched under a non-default profile reports current.

## Notes

The generic regression and full verification passed on the prior integration head. The clean replacement must preserve the recorded-identity reconstruction and rerun operator replay.
