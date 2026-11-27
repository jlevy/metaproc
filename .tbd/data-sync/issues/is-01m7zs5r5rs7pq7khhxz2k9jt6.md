---
type: is
id: is-01m7zs5r5rs7pq7khhxz2k9jt6
title: Preserve inherited variables in nested resource snapshots
kind: bug
status: open
priority: 2
version: 1
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m7p3pwxkys2pdd65g2kagg7b
created_at: 2026-11-21T19:26:18.294Z
updated_at: 2026-11-21T19:26:18.294Z
---
Snapshot recursion passes raw step.with_ and can omit a grandchild topology/budget when its process path uses an inherited variable. Runtime copies ancestor variables and resolves with bindings. Align snapshot parameter inheritance with execution and cover a templated nested process path.
