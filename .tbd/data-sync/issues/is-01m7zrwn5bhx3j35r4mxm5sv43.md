---
type: is
id: is-01m7zrwn5bhx3j35r4mxm5sv43
title: Report incomplete Codex auth JSON as unverified
kind: bug
status: closed
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m7p3pwxkys2pdd65g2kagg7b
created_at: 2026-11-21T19:21:20.290Z
updated_at: 2026-11-21T19:33:24.722Z
closed_at: 2026-11-21T19:33:24.722Z
close_reason: "Implemented on the #126 consolidation head fb6e46b. Regression and review evidence is recorded per finding in the pull request dispositions."
resolution: null
duplicate_of: null
---
PR #126 F1. Treat checked file-backed Codex credentials as unverified when the selected mode lacks its credential field. Preserve the active credential source.
