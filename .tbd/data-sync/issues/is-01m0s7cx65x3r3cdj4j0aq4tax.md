---
type: is
id: is-01m0s7cx65x3r3cdj4j0aq4tax
title: Preserve retry-later signals across GCP dispatch boundaries
kind: bug
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
created_at: 2026-08-24T06:30:19.076Z
updated_at: 2026-08-24T06:30:19.076Z
---
GCP Batch polling collapses every failed worker/orchestrator to exit code 1. Preserve exit-78 semantics and whole-step checkpoint ownership across worker and orchestrator boundaries so signal mode remains resumable instead of becoming a generic failure.
