---
type: is
id: is-01m0rwnn73b72kw4yvxp2jc1z7
title: Introduce shared recursive RunExecutionContext and leaf semaphore
kind: feature
status: open
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0rwnmteqp36efc2xar0v5fd
  - type: blocks
    target: is-01m0rwnnkmyr1agx7y3hfsbk30
parent_id: is-01m0rs7df0g28zgnsykar366kb
created_at: 2026-08-24T03:22:51.491Z
updated_at: 2026-08-24T03:23:05.251Z
---
Replace recursive run-policy argument expansion with one immutable internal context, reuse one leaf semaphore and cancellation signal through composite recursion, and remove the dead external-semaphore seam.
