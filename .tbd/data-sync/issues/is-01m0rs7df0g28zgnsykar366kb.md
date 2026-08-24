---
type: is
id: is-01m0rs7df0g28zgnsykar366kb
title: Unify recursive run policy and nonblocking execution
kind: feature
status: in_progress
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93je6fk789d26aef6wx11
  - type: blocks
    target: is-01m0r93k0sfy1ye28jj2f7db1z
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-24T02:22:39.072Z
updated_at: 2026-08-24T03:20:32.432Z
---
Introduce one RunExecutionContext after PR #31 merges. Share the run semaphore, cancellation, backend/profile/auth/admission policy, and explicit force/skip/continue semantics through recursion; propagate credential pools to scalar agents; move command-backed code work off the event loop; and use an explicitly sized run-owned executor with characterization tests.
