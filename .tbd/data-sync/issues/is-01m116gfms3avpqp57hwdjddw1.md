---
type: is
id: is-01m116gfms3avpqp57hwdjddw1
title: Do not require parent task state for scalar composite scopes
kind: bug
status: closed
priority: 0
version: 4
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - runtime-projection
  - validation
dependencies: []
parent_id: is-01m10c27jjs2qh7hbcn3msz564
created_at: 2026-08-27T08:48:43.160Z
updated_at: 2026-08-27T08:59:13.453Z
closed_at: 2026-08-27T08:59:13.440Z
close_reason: "Fixed in 114da54: scalar composites project through child scope state, mapped composites retain parent item task records, and full verification plus real-layout regression pass."
resolution: null
duplicate_of: null
---
TaskOutputProjection currently reports task-state-missing when a scalar composite has the normal durable representation: a child scope run plan, process status, and leaf task records, with no parent scalar task record. Treat child scope state as the scalar composite coverage authority, retain strict parent item task coverage for mapped composites, and retain existing projection of any historical scalar-composite parent task record. Add a real-layout regression and verify the public observer remains read-only.

## Notes

Root cause reproduced against a hydrated completed run: scalar composite steps have child scope state and leaf task records but no parent scalar task status. Implemented the consumer-neutral rule that scalar composites project as scopes, while mapped composites retain parent item tasks. Focused projection plus real run-process regression: 35 passed. Ruff and BasedPyright are clean. The private consumer exporter now completes without coverage gaps; no consumer details are being published here.
