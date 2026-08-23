---
type: is
id: is-01m0rady1w8hjdbjz5gvkb5qy8
title: Enforce retry budgets across resumes from attempt history
kind: bug
status: open
priority: 1
version: 1
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r93gwcj17mn4dmw1ts7fqa
created_at: 2026-08-23T22:04:04.028Z
updated_at: 2026-08-23T22:04:04.028Z
---
Current retry loops reset their local attempt counter on every run-process or run-parallel resume. Once exact attempt history is durable, resolve remaining attempts for the current task generation from that history so repeated resumes cannot exceed the authored retry budget. Preserve explicit force semantics by moving forced work to a new generation rather than erasing history.
