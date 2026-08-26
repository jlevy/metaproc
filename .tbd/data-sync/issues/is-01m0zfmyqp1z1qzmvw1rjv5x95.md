---
type: is
id: is-01m0zfmyqp1z1qzmvw1rjv5x95
title: "Reconcile 'task': pivotal object vs runtime-only term"
kind: task
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
  - terminology
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:58.006Z
updated_at: 2026-08-26T16:49:58.006Z
---
process-framework-concepts.md: 'The task is the pivotal object in this model, because it is the correct unit of scheduling, of failure, and of resume.' (64 uses). metaproc-concepts-and-principles.md 4.2: 'task is a runtime term used by state and log paths; it is not an authored process object or a synonym for item.' (8 uses). Both are defensible about different layers - authored surface vs runtime - but neither says which layer it means. State the scope in each.
