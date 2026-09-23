---
type: is
id: is-01m15p7eh62qnw6ghk8jkm141w
title: Fold run-plan roster refresh into the discovery-at-dispatch boundary
kind: task
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-08-29T02:40:22.053Z
updated_at: 2026-08-29T02:40:22.053Z
---
PR #49 round-6 suggestion. _refresh_run_plan_item_keys must be called at four dispatch sites in commands/run_process.py (code fan-out, aligned chain, composite fan-out, agent fan-out). A future fan-out mode that forgets the call leaves stale key authority and its items vanish from the projection with no coverage gap. Make the invariant structural rather than remembered.
