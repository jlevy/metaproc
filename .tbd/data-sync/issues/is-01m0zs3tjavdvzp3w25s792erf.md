---
type: is
id: is-01m0zs3tjavdvzp3w25s792erf
title: "PR #49 review H6: expose runtime tasks and outputs in the actual browser view"
kind: bug
status: in_progress
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
dependencies: []
parent_id: is-01m0zs1svbsptksz66728wzdrb
created_at: 2026-08-26T19:35:22.442Z
updated_at: 2026-08-26T19:47:28.110Z
---
domain_views.js does not send run_dir and no browser surface consumes task_projection. Wire the active run context and a minimal inspectable task/output view, or explicitly document API-only scope and keep view completion open.

## Notes

Fixed in the shared PR #49 working tree, pending parent integration commit: the existing Resources view derives run_dir from the active resources.json path, reuses the existing viz-model data hook and immutable run-config process identity, and renders a minimal task/output table from the flattened public projection. Accepted artifacts are clickable; rejected outputs show their reason. No new store, route, or persisted state. Focused suite: 38 passed; browser/type/lint gates clean. Residual: a hydrated run whose recorded absolute process_spec is unavailable under the served root shows a typed runtime-projection warning rather than tasks.
