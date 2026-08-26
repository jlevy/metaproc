---
type: is
id: is-01m0zpdfftvam4ebsvsvq10wve
title: Bare resume re-enters completed scalar composites
kind: bug
status: open
priority: 1
version: 1
labels:
  - resume
  - composite
dependencies: []
created_at: 2026-08-26T18:48:13.039Z
updated_at: 2026-08-26T18:48:13.039Z
---
run-process snapshots prior process state and rewrites process-status entries to pending before the level completion check. Completed scalar composites therefore re-enter their child orchestrators on a bare same-RUN_ID resume, even when child leaves later skip from task state. Add a focused regression and avoid structural recomputation when the completed composite boundary remains valid.
