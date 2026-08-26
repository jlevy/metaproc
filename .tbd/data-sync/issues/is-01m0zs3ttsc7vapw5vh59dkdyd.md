---
type: is
id: is-01m0zs3ttsc7vapw5vh59dkdyd
title: "PR #49 review H7: use a total ordering for cross-scope task keys"
kind: bug
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
dependencies: []
parent_id: is-01m0zs1svbsptksz66728wzdrb
created_at: 2026-08-26T19:35:22.713Z
updated_at: 2026-08-26T20:01:31.199Z
closed_at: 2026-08-26T20:01:31.199Z
close_reason: "Fixed and validated in e1b9de2; per-finding disposition published on PR #49 and all five CI jobs passed."
resolution: null
duplicate_of: null
---
Sorting TaskKey directly can compare None with str for otherwise valid scalar/mapped instances of the same step name across scopes. Use an explicit total sort key and regression coverage.
