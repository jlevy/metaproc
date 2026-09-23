---
type: is
id: is-01m0rwnmteqp36efc2xar0v5fd
title: Move synchronous step work to a run-owned executor
kind: feature
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0rwnp06bhsk91k8kw1szh2g
parent_id: is-01m0rs7df0g28zgnsykar366kb
created_at: 2026-08-24T03:22:51.086Z
updated_at: 2026-08-24T03:56:55.292Z
closed_at: 2026-08-24T03:56:55.292Z
close_reason: Implemented and fully verified in 30644fd; remaining scalar auth and cancellation safety stay open as mp-bvjd and mp-l6b5.
resolution: null
duplicate_of: null
---
Run command-backed code and synchronous handlers off the event loop through one explicitly sized executor owned by the top-level run; prove sibling progress and executor ceiling.
