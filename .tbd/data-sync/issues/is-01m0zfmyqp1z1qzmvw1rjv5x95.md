---
type: is
id: is-01m0zfmyqp1z1qzmvw1rjv5x95
title: "Reconcile 'task': pivotal object vs runtime-only term"
kind: task
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
  - terminology
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:58.006Z
updated_at: 2026-08-27T15:07:55.799Z
closed_at: 2026-08-27T15:07:55.799Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 6. General doc: task is 'the pivotal object in this model ... the correct unit of scheduling, of failure, and of resume' (64 uses). Concepts doc: 'a runtime term used by state and log paths; it is not an authored process object' (8 uses).

Both are defensible about different layers - the general doc is describing the model, the concepts doc the authored surface. Neither says which layer it means, so they read as a flat contradiction.

State the layer in each. Both documents ship after this plan, so a reader can hold both definitions at once and has no way to reconcile them.
