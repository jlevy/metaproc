---
type: is
id: is-01m10za84cxdzqqen0qa9w4x6d
title: Remove the repository-maintenance blockquote from the eight moved docs
kind: task
status: open
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies:
  - type: blocks
    target: is-01m10zav5m1qxns81taqs7m9d8
  - type: blocks
    target: is-01m10za924d7rd3snc610kyhxt
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:58.828Z
updated_at: 2026-08-27T06:43:43.772Z
---
Phase 4. All eight carry a Maintenance blockquote: 'Revise via tbd shortcut revise-architecture-doc', 'bump the last updated date above', and a pointer to development.md section Architecture docs.

That is an instruction to a contributor to this repository, shipped to a reader who may have neither the repository nor tbd. Remove it from all eight and state the revision convention once, in docs/project/README.md.

The companion-docs list inside the same blockquote also needs fixing before it moves anywhere - see the companion-list bead.
