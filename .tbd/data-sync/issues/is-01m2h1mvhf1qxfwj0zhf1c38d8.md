---
type: is
id: is-01m2h1mvhf1qxfwj0zhf1c38d8
title: "PR #82 review R2: Make native transcript copying resistant to symlink races"
kind: bug
status: closed
priority: 0
version: 3
labels: []
dependencies: []
parent_id: is-01m2h1m2v9ayz833z46hqah02t
created_at: 2026-09-14T22:47:16.268Z
updated_at: 2026-09-14T23:24:18.415Z
closed_at: 2026-09-14T23:24:18.415Z
close_reason: Addressed in the PR branch with namespace isolation, descriptor-safe all-or-nothing capture, protocol compatibility, nonblocking completion processing, documented collision semantics, complete operator/design docs, focused regressions, GTIA compatibility checks, and a green make verify gate.
resolution: null
duplicate_of: null
---
PR #82 review R2 (Blocker). slot_dir itself is not checked, planned paths can be redirected before copy, and a final symlink is copied despite the documented contract. Use handle-based no-follow traversal/copy, fail the set on any unsafe component, and test slot-root, parent, and final-component swaps.
