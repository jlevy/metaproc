---
type: is
id: is-01m0rs7df0g28zgnsykar366kb
title: Unify recursive run policy and nonblocking execution
kind: feature
status: in_progress
priority: 1
version: 11
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
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
updated_at: 2026-08-24T19:03:43.197Z
---
Introduce one RunExecutionContext after PR #31 merges. Share the run semaphore, cancellation, backend/profile/auth/admission policy, and explicit force/skip/continue semantics through recursion; propagate credential pools to scalar agents; move command-backed code work off the event loop; and use an explicitly sized run-owned executor with characterization tests.
