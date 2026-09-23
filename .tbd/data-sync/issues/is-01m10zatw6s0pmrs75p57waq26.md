---
type: is
id: is-01m10zatw6s0pmrs75p57waq26
title: Add a reading guide to the design doc
kind: task
status: closed
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:43:18.022Z
updated_at: 2026-08-27T15:07:54.673Z
closed_at: 2026-08-27T15:07:54.673Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 5. 56 numbered sections, no map. The doc's own Potential Improvements proposes the fix: 'Add a Reading Guide section at the top to help readers navigate the more than 21 sections by use case (operator, process author, adapter implementer, framework contributor).'

This is the single largest usability problem in the shipped set and it gets worse when the doc becomes a 30k-token CLI response an agent asks for by name. Put the guide at the top, before section 5, keyed by what the reader is trying to do.
