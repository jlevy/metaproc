---
type: is
id: is-01m0r92q2y1pe7dmhrcj6nst7q
title: Production task scheduler and mapped composite scopes
kind: epic
status: in_progress
priority: 1
version: 10
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
child_order_hints:
  - is-01m0r93gwcj17mn4dmw1ts7fqa
  - is-01m0r93hdc3x84yqjwf2a3xn03
  - is-01m0r93hy045zzjtyw4brakhaw
  - is-01m0r93je6fk789d26aef6wx11
  - is-01m0r93k0sfy1ye28jj2f7db1z
  - is-01m0r93kk96jbzs27d9fmx762k
  - is-01m0r93m6cz6dytw4c1m2bbyaj
  - is-01m0r93mr72xw9k0p8tn94a07d
created_at: 2026-08-23T21:40:27.869Z
updated_at: 2026-08-23T21:41:27.913Z
---
Adopt the executable reference model in the production engine so a wide downstream workflow can compile mapped multi-step scopes into one task graph. Own durable task facts, closed expansions, dependency clauses, ready-task dispatch, resource-neutral composites, universal admission, and browsable artifact provenance. Opaque nested child schedulers are explicitly out of scope.
