---
type: is
id: is-01m0s0r624c0eszrgnq4qgjjbe
title: Wire the documented auth retry-later policy into dispatch
kind: bug
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - authentication
  - execution-model
dependencies: []
parent_id: is-01m0rs7df0g28zgnsykar366kb
created_at: 2026-08-24T04:34:08.579Z
updated_at: 2026-08-24T04:34:08.579Z
---
The authentication architecture documents --auth-retry-later={fail-fast,wait,signal}, bounded max-wait behavior, and retry_later.yaml checkpoints, but run-process/run-parallel expose no such CLI option and no dispatch caller writes the checkpoint consumed by resume-daemon. Fan-out currently performs an unconditional internal cooling retry while scalar pool exhaustion uses fail-fast. Define one typed run policy, wire it through RunExecutionContext and both scalar/fan-out dispatch, preserve the invariant that pool waiting is not an execution attempt, and add deterministic wait/signal/fail-fast tests. Update docs to distinguish shipped behavior until this lands.
