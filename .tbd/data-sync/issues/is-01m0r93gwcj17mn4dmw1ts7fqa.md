---
type: is
id: is-01m0r93gwcj17mn4dmw1ts7fqa
title: Production task records, fenced commits, and replay parity
kind: feature
status: in_progress
priority: 1
version: 14
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r93hdc3x84yqjwf2a3xn03
  - type: blocks
    target: is-01m0r93hy045zzjtyw4brakhaw
  - type: blocks
    target: is-01m0r93kk96jbzs27d9fmx762k
  - type: blocks
    target: is-01m0r93m6cz6dytw4c1m2bbyaj
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
child_order_hints:
  - is-01m0r9d159n3zwmm2hxcjzq6x1
  - is-01m0r9d1k3vevegz15170dtvcf
  - is-01m0r9d1zvx40602kj7egnnqas
  - is-01m0r9d2ckfh04hjx3v2qgafy7
  - is-01m0rady1w8hjdbjz5gvkb5qy8
  - is-01m0ragf2xm87db9bbwhv7ys57
created_at: 2026-08-23T21:40:54.283Z
updated_at: 2026-08-24T00:51:39.502Z
---
Map the reference model onto production records: versioned task keys and generations, append-only attempts, attempt-private staging, one fenced validated commit, compatibility readers, and trace replay that makes model-versus-engine disposition differences fail.

## Notes

Metaproc PR #31 (commit 7563843) completed the append-only attempt-history and exact-replay slice, including crash recovery and status projection. Keep this parent open: attempt-private staging, accepted commit manifests, complete generation/fence publication semantics, compatibility integration, and model-versus-engine trace parity remain for later stacked PRs.
