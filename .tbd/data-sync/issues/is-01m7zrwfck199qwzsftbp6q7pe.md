---
type: is
id: is-01m7zrwfck199qwzsftbp6q7pe
title: Preserve resolved execution profiles in Batch workers
kind: bug
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m7p3pwxkys2pdd65g2kagg7b
created_at: 2026-11-21T19:21:14.380Z
updated_at: 2026-11-21T19:33:24.684Z
closed_at: 2026-11-21T19:33:24.684Z
close_reason: "Implemented on the #126 consolidation head fb6e46b. Regression and review evidence is recorded per finding in the pull request dispositions."
resolution: null
duplicate_of: null
---
PR #126 E1. Make Filestore Batch workers consume the resolved scope step rather than recreating a plan from current profiles and losing a recorded resume override.
