---
type: is
id: is-01kzkx35fq4affy9jgee8h02cp
title: "PR #2 review R1: reconcile stale branch without reverting releases"
kind: bug
status: closed
priority: 0
version: 2
labels: []
dependencies: []
parent_id: is-01kzkwt9ddwj9sfvjwzt7ma027
created_at: 2026-08-09T18:38:20.149Z
updated_at: 2026-08-09T18:51:39.419Z
closed_at: 2026-08-09T18:51:39.418Z
close_reason: Resolved in 83b894d; focused contracts, make verify, pre-push verification, and all fresh GitHub checks pass.
---
Merge current main into PR #2, resolve conflicts deliberately, preserve v0.2.0 and all later behavior, and verify the final diff.
