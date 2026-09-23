---
type: is
id: is-01m2h1mx4bxxw1bsqw6cc4nt7h
title: "PR #82 review R3: Prevent incomplete native transcript publication"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m2h1m2v9ayz833z46hqah02t
created_at: 2026-09-14T22:47:17.887Z
updated_at: 2026-09-14T23:24:18.422Z
closed_at: 2026-09-14T23:24:18.422Z
close_reason: Addressed in the PR branch with namespace isolation, descriptor-safe all-or-nothing capture, protocol compatibility, nonblocking completion processing, documented collision semantics, complete operator/design docs, focused regressions, GTIA compatibility checks, and a green make verify gate.
resolution: null
duplicate_of: null
---
PR #82 review R3 (High). Scan and copy failures currently publish a partial directory when any file succeeds. Make the set all-or-nothing or publish explicit completeness metadata; add a one-of-many failure test.
