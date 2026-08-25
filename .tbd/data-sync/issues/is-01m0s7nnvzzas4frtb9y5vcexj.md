---
type: is
id: is-01m0s7nnvzzas4frtb9y5vcexj
title: Wire retry-later policy through auth dispatch configuration
kind: task
status: closed
priority: 1
version: 6
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
delegate: codex
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
created_at: 2026-08-24T06:35:06.478Z
updated_at: 2026-08-25T19:18:18.861Z
closed_at: 2026-08-24T07:27:06.417Z
close_reason: "Implemented and verified in draft PR #36 at bb174f6: typed retry-later policy and bounded wait transport through both CLIs, run-config, local dispatch, orchestrator, and worker boundaries; shared AuthPoolFlags also fixes cloud auth_policy loss. Local and five-job GitHub CI are green. Release behavior remains open under the sibling mp-tibt beads."
resolution: null
duplicate_of: null
---
Define one typed retry-later policy configuration with fail-fast defaults and a bounded maximum wait, expose it on run-process and run-parallel, and propagate it without loss through AuthPoolFlags, run-config snapshots, local PoolDispatchConfig, orchestrator dispatch, and worker dispatch. This slice must remain behavior-neutral until the scheduler-specific beads consume the policy.

## Notes

Superseded after senior review: the retry-later transport implemented here was deleted rather than expanded without a consumer. The independently useful complete AuthPoolFlags cloud transport was retained and folded into PR #34 at 3d11a64. Audit/removal decisions now live under mp-tibt.
