---
type: is
id: is-01m0s7nnvzzas4frtb9y5vcexj
title: Wire retry-later policy through auth dispatch configuration
kind: task
status: in_progress
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
delegate: codex
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
created_at: 2026-08-24T06:35:06.478Z
updated_at: 2026-08-24T06:35:11.066Z
---
Define one typed retry-later policy configuration with fail-fast defaults and a bounded maximum wait, expose it on run-process and run-parallel, and propagate it without loss through AuthPoolFlags, run-config snapshots, local PoolDispatchConfig, orchestrator dispatch, and worker dispatch. This slice must remain behavior-neutral until the scheduler-specific beads consume the policy.

## Notes

First stacked implementation slice on codex/gtia-v3-retry-later. TDD started with AuthPoolFlags env/CLI round-trip; complete typed runtime defaults and all local/cloud propagation before opening the PR.
