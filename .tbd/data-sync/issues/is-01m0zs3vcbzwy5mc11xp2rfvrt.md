---
type: is
id: is-01m0zs3vcbzwy5mc11xp2rfvrt
title: "PR #49 review M9: diagnose nonportable external outputs without aborting projection"
kind: bug
status: open
priority: 2
version: 1
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
dependencies: []
parent_id: is-01m0zs1svbsptksz66728wzdrb
created_at: 2026-08-26T19:35:23.275Z
updated_at: 2026-08-26T19:35:23.275Z
---
Metaproc permits some valid outputs outside the run tree, but the hydrated browser projection currently aborts the entire scan. Distinguish portable/hydratable outputs from external recorded paths and keep them diagnostic rather than consumable.
