---
type: is
id: is-01m10za8p2y72rq1zbs27xhfxv
title: Fix the design doc header drift the extraction exposes
kind: bug
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:59.394Z
updated_at: 2026-08-27T15:07:51.671Z
closed_at: 2026-08-27T15:07:51.671Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 4, docs/arch/arch-metaproc-core.md header block (currently lines 7-24).

Two inconsistencies:
- The header says 'Revision: rev2m' while the newest Revision History entry is rev2o.
- The header says 'Date: 2026-03-23 (last updated 2026-08-24)' while rev2o is dated 2026-08-25.

The revision line is deleted by the Revision History extraction, which resolves the first. The last-updated date still needs correcting, and once the doc ships, a stale date is visible to every downstream reader rather than just to contributors.

Related: mp-pw6z proposes a devtools check for arch-doc date drift. This is the case that proves it is needed.
