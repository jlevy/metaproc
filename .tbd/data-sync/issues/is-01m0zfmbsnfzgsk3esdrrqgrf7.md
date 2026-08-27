---
type: is
id: is-01m0zfmbsnfzgsk3esdrrqgrf7
title: Move the architecture index into docs/project/README.md
kind: task
status: closed
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:38.613Z
updated_at: 2026-08-27T15:07:49.774Z
closed_at: 2026-08-27T15:07:49.773Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 2. docs/development.md currently owns the arch-doc index and it is exactly in sync with disk. Keep it that way through the move.

The index now describes documents that live in src/metaproc/docs/, not docs/arch/, and every one of them is a 'metaproc help' topic. The index gains a topic-name column and the paths all change. Leave a pointer in development.md.

Also update the arch-doc convention text there: the docs no longer live in a directory of their own, so 'docs/arch/' as an organizing idea has to be restated as the 'arch-' filename prefix - or dropped, which is the Phase 3 cohesion question.
