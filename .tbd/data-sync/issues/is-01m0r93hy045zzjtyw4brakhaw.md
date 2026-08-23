---
type: is
id: is-01m0r93hy045zzjtyw4brakhaw
title: Run the production engine as a ready-task scheduler
kind: feature
status: open
priority: 1
version: 4
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93je6fk789d26aef6wx11
  - type: blocks
    target: is-01m0r93k0sfy1ye28jj2f7db1z
  - type: blocks
    target: is-01m0r93m6cz6dytw4c1m2bbyaj
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-23T21:40:55.360Z
updated_at: 2026-08-23T21:40:57.675Z
---
Replace the opted-in level walk with task-level ready-set dispatch over persisted clauses while preserving legacy barrier semantics by version. Separate task mapping, invocation and governance; implement structured succeeded and finished requirements plus same-key, broadcast and collect-all bindings.
