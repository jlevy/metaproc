---
type: is
id: is-01m0zfmy8kjkexwgbmk5d6dctk
title: Reconcile 'roster' across the three docs
kind: task
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
  - terminology
dependencies:
  - type: blocks
    target: is-01m0zfnd8tfw2a906p8p7j5ytr
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:57.523Z
updated_at: 2026-08-26T16:50:12.890Z
---
metaproc-concepts-and-principles.md line 399 states 'Analysis-domain code uses roster as a synonym; the framework does not.' arch-metaproc-core.md line 68 states the same invariant, then uses roster 32 times - including as a value in its own role enum (line 432: process, template, packet, roster, run-input) and in step ids and dep names in its reference examples. process-framework-concepts.md defines roster as a core term and uses it 25 times. Pick one and make all three agree; the role enum makes 'the framework does not use it' hard to defend.
