---
type: is
id: is-01m10zatjx1pt57vatcdfgzgdn
title: Cut the design doc section 21 / arch-cloud-execution duplication
kind: task
status: closed
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies:
  - type: blocks
    target: is-01m10zav5m1qxns81taqs7m9d8
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:43:17.725Z
updated_at: 2026-08-27T15:07:54.396Z
closed_at: 2026-08-27T15:07:54.395Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 5. The design doc's own Potential Improvements says it: 'Consolidate the cloud execution summary (section 21) further: much of its content is now covered in arch-cloud-execution.md, and the duplication creates maintenance burden.'

Section 21 is roughly 350 lines. arch-cloud-execution.md is 6,178 words. Once both are topics in the same listing, a reader has no way to know which is authoritative.

Leave a short orientation in section 21 and point at the arch doc for the detail, or the reverse - but one of them owns cloud execution.
