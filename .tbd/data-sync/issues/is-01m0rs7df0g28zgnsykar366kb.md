---
type: is
id: is-01m0rs7df0g28zgnsykar366kb
title: Unify recursive run policy and nonblocking execution
kind: feature
status: closed
priority: 1
version: 15
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93je6fk789d26aef6wx11
  - type: blocks
    target: is-01m0r93k0sfy1ye28jj2f7db1z
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
child_order_hints:
  - is-01m0rwnmdgasw9grzxvzjsdrc5
  - is-01m0rwnmteqp36efc2xar0v5fd
  - is-01m0rwnn73b72kw4yvxp2jc1z7
  - is-01m0rwnnkmyr1agx7y3hfsbk30
  - is-01m0rwnp06bhsk91k8kw1szh2g
  - is-01m0s0r624c0eszrgnq4qgjjbe
  - is-01m0t7zs3etjttp22nytn7abcn
created_at: 2026-08-24T02:22:39.072Z
updated_at: 2026-08-25T19:31:21.933Z
closed_at: 2026-08-25T19:31:21.932Z
close_reason: One recursive execution context now carries run policy, shared admission, cancellation, executor, credential settings, and pool ownership; optional API cleanup and retry audit remain separately paused.
resolution: null
duplicate_of: null
---
Use one RunExecutionContext for recursive scopes. Share cancellation, backend/profile/auth/admission policy, force/skip/continue semantics, the executable-leaf ceiling, and the run-owned synchronous executor; propagate credential policy to scalar agents and keep command-backed code off the event loop with owned cancellation cleanup.
