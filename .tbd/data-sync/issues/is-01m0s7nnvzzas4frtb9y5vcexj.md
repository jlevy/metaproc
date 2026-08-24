---
type: is
id: is-01m0s7nnvzzas4frtb9y5vcexj
title: Wire retry-later policy through auth dispatch configuration
kind: task
status: in_progress
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
delegate: codex
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
created_at: 2026-08-24T06:35:06.478Z
updated_at: 2026-08-24T06:59:02.915Z
---
Define one typed retry-later policy configuration with fail-fast defaults and a bounded maximum wait, expose it on run-process and run-parallel, and propagate it without loss through AuthPoolFlags, run-config snapshots, local PoolDispatchConfig, orchestrator dispatch, and worker dispatch. This slice must remain behavior-neutral until the scheduler-specific beads consume the policy.

## Notes

Implementation complete on codex/gtia-v3-retry-later. Precommit senior review approved the configuration boundary after replacing OrchestratorDispatchConfig's duplicated auth fields with the shared AuthPoolFlags payload already called for by arch-cloud-execution; this also fixes existing cloud auth_policy loss. Both CLIs, run-config, local PoolDispatchConfig, orchestrator env, and worker CLI transport now carry a validated fail-fast|wait|signal policy plus positive bounded max wait. Architecture docs updated. Validation: make lint-check green; full suite 4,295 passed, 8 skipped. Release gate: publish as draft and do not merge/mark ready until scheduler behavior beads mp-f5m5/mp-l3ot/mp-txt9 and signal/cloud follow-ons consume the options.
