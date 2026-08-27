---
type: is
id: is-01m10za84cxdzqqen0qa9w4x6d
title: Remove the repository-maintenance blockquote from the eight moved docs
kind: task
status: closed
priority: 1
version: 4
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
updated_at: 2026-08-27T15:07:51.108Z
closed_at: 2026-08-27T15:07:51.108Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 4. All eight carry a Maintenance blockquote: 'Revise via tbd shortcut revise-architecture-doc', 'bump the last updated date above', and a pointer to development.md section Architecture docs.

That is an instruction to a contributor to this repository, shipped to a reader who may have neither the repository nor tbd. Remove it from all eight and state the revision convention once, in docs/project/README.md.

The companion-docs list inside the same blockquote also needs fixing before it moves anywhere - see the companion-list bead.
