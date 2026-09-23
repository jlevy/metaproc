---
type: is
id: is-01m0zfmy8kjkexwgbmk5d6dctk
title: Reconcile 'roster' across the three docs
kind: task
status: closed
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
  - terminology
dependencies:
  - type: blocks
    target: is-01m0zfnd8tfw2a906p8p7j5ytr
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:57.523Z
updated_at: 2026-08-27T15:07:55.485Z
closed_at: 2026-08-27T15:07:55.485Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 6. The concepts doc says 'Analysis-domain code uses roster as a synonym; the framework does not.' The general doc defines roster as a core term and uses it 24 times. The design doc uses it 32 times, including as a value in its own role enum and throughout its reference examples.

So the shipped doc denies a term that the shipped design doc uses 32 times - and after this plan both ship in the same wheel, where a reader can hit the contradiction in two commands. Decide one way and make all three agree.
