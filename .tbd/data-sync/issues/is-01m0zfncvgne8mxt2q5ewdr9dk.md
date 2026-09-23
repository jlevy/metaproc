---
type: is
id: is-01m0zfncvgne8mxt2q5ewdr9dk
title: Add cross-reference headers to both concepts docs
kind: task
status: closed
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
  - terminology
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:50:12.464Z
updated_at: 2026-08-27T15:07:56.867Z
closed_at: 2026-08-27T15:07:56.867Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Each concepts doc should name the other in its header and say which owns what: the shipped doc owns what Metaproc does today, the general doc owns the model. Adopt the spec's rule - where they disagree about Metaproc, the shipped doc wins; where they disagree about the model, the general doc wins and the shipped doc gains a pointer.
