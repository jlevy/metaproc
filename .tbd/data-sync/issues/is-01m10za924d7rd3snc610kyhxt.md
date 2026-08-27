---
type: is
id: is-01m10za924d7rd3snc610kyhxt
title: Correct the design doc companion-docs list
kind: bug
status: open
priority: 2
version: 1
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:59.780Z
updated_at: 2026-08-27T06:42:59.780Z
---
Phase 4, in the Maintenance blockquote of docs/arch/arch-metaproc-core.md.

The list of companion docs links to arch-metaproc-core.md - itself - and omits arch-execution-model.md and arch-file-io-utilities.md, two of the seven real companions.

The blockquote is being removed anyway, so the fix is to make sure the replacement pointer (wherever the companion set ends up being named) lists all seven and does not self-link. Worth its own bead because a wrong companion list is how the next reader learns the wrong shape of the system.
