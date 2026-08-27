---
type: is
id: is-01m0zfmbchshq1m9evnysb8ctx
title: Move the remaining repo-internal docs under docs/project
kind: task
status: open
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:38.193Z
updated_at: 2026-08-27T06:41:45.761Z
---
Phase 4. Scope narrowed: conventions.md and artifact-catalog.md are NOT in this bead any more - they ship in the wheel (see the move bead), which settles the Open Question the earlier version of this bead pointed at.

git mv under docs/project/:
- docs/releases/ (v0.2.0.md, v0.2.1.md, v0.3.0.md)
- docs/performance-notes.md
- docs/memory-accounting-reference.md
- docs/agent-toolchain-bootstrap.md
- docs/publishing.md

Stays at the top level of docs/: installation.md (user entry), development.md (contributor entry), runbooks/ (operator procedures for this repo).

After this, docs/ holds two files, one directory of runbooks, and project/.
