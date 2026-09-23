---
type: is
id: is-01m0vqngx1ergbsmwwcn9mtz8x
title: "Restack: keep unmerged stack changes out of released 0.3.0 changelog"
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0vhr5rv34k6cbvr6wqx24sw
created_at: 2026-08-25T05:53:07.475Z
updated_at: 2026-08-25T05:55:27.061Z
closed_at: 2026-08-25T05:55:27.060Z
close_reason: Fixed in 765ed5a; all unmerged stack entries are now under Unreleased and the 0.3.0 section matches released main.
resolution: null
duplicate_of: null
---
The post-release rebase left PR #33/#34 execution-context and scalar-auth changelog entries under the already-cut 0.3.0 section. Move all unmerged stack entries to Unreleased before continuing the #35/#37 restack, and preserve the historical 0.3.0 section exactly. Discovered while resolving PR #35 conflicts.
