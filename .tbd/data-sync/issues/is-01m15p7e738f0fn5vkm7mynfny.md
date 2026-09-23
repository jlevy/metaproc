---
type: is
id: is-01m15p7e738f0fn5vkm7mynfny
title: Quarantine projection validation errors per scope
kind: task
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-08-29T02:40:21.730Z
updated_at: 2026-08-29T02:40:21.730Z
---
PR #49 round-6 suggestion. One inconsistent record (symlinked status.yaml, half-hydrated attempts/ dir) raises out of scan_task_output_projection and degrades the entire task view to a single warning. Partial hydration is when the view is most wanted. Contain per-scope validation failures as typed diagnostics while other scopes still render. Evolution of the H5/R20 warning-plus-structural-graph choice, not a defect.
