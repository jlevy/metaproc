---
type: is
id: is-01m0rwnnkmyr1agx7y3hfsbk30
title: Propagate scalar credential-pool policy
kind: feature
status: open
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0rwnp06bhsk91k8kw1szh2g
parent_id: is-01m0rs7df0g28zgnsykar366kb
created_at: 2026-08-24T03:22:51.891Z
updated_at: 2026-08-24T03:56:54.820Z
---
Pass the run-context auth flags and pool-dispatch configuration into scalar agent execution, lease credentials through the same framework machinery as fan-out, and assert the actual label used by a child invocation. Recursive force, root-skip, continue, backend, profile, and admission-posture semantics landed in 30644fd.
