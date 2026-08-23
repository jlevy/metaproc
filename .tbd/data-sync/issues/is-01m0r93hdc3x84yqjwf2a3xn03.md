---
type: is
id: is-01m0r93hdc3x84yqjwf2a3xn03
title: Persist the recursive resolved graph and closed expansions
kind: feature
status: open
priority: 1
version: 3
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93hy045zzjtyw4brakhaw
  - type: blocks
    target: is-01m0r93kk96jbzs27d9fmx762k
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-23T21:40:54.827Z
updated_at: 2026-08-23T21:40:57.064Z
---
Persist process semantics, recursively resolved templates, explicit dependency clauses, expansion generations, key spaces, closure and derived-roster lineage. Resume executes the persisted resolution and fan-in cannot treat a still-materializing roster as complete.
