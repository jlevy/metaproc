---
type: is
id: is-01m0s0r624c0eszrgnq4qgjjbe
title: Wire the documented auth retry-later policy into dispatch
kind: bug
status: in_progress
priority: 1
version: 9
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
delegate: codex
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0rs7df0g28zgnsykar366kb
child_order_hints:
  - is-01m0s7cvkpk6pmhzenf9stzmgr
  - is-01m0s7cw0ghtj387wj4nar45we
  - is-01m0s7cwczrk6a655t1gjxtfnj
  - is-01m0s7cwsmee3fj0gehfkfz329
  - is-01m0s7cx65x3r3cdj4j0aq4tax
  - is-01m0s7cxknep5425wm4z90bcqj
  - is-01m0s7cy280m06mznmrggw55ka
created_at: 2026-08-24T04:34:08.579Z
updated_at: 2026-08-24T06:30:19.976Z
---
The authentication architecture documents --auth-retry-later={fail-fast,wait,signal}, bounded max-wait behavior, and retry_later.yaml checkpoints, but run-process/run-parallel expose no such CLI option and no dispatch caller writes the checkpoint consumed by resume-daemon. Fan-out currently performs an unconditional internal cooling retry while scalar pool exhaustion uses fail-fast. Define one typed run policy, wire it through RunExecutionContext and both scalar/fan-out dispatch, preserve the invariant that pool waiting is not an execution attempt, and add deterministic wait/signal/fail-fast tests. Update docs to distinguish shipped behavior until this lands.

## Notes

Implementation started as the next PR stacked on Metaproc #35. TDD scope: one typed fail-fast/wait/signal policy on RunExecutionContext, shared scalar and fan-out exhaustion handling, bounded waiting that does not consume an execution attempt, durable retry_later.yaml signal/checkpoint behavior, and deterministic tests plus docs.
