---
type: is
id: is-01kz3c2vm173mzycwksmsp4qp5
title: Define V2 resource contracts and deterministic ledger reconciliation
kind: feature
status: closed
priority: 1
version: 8
spec_path: docs/project/specs/done/plan-2026-08-03-focused-resource-observability.md
labels:
  - resources
  - pr-10
dependencies:
  - type: blocks
    target: is-01kz2y3hdgz6zveescccx821xh
  - type: blocks
    target: is-01kz3c3a7jwqvxjhwpkkm6xyfy
  - type: blocks
    target: is-01kz2y3jccrsgpfe375naa07dg
parent_id: is-01kz3c2j1me5ng0pkjym3k42rr
created_at: 2026-08-03T08:33:13.344Z
updated_at: 2026-08-09T18:56:50.762Z
closed_at: 2026-08-03T10:07:21.502Z
close_reason: "Implemented and reviewed in Metaproc commit 6dcb522: strict contracts, canonical evidence, usage/meters, ledger projection, immutable budgets, summary/operator views, causal finalization, and inactive local recovery."
---
Add strict V1/V2 dispatch, stable event identities, provider/meter/coverage/terminal/budget result models, and deterministic duplicate reconciliation. Preserve measured zero, use a timestamp sentinel when evidence has no time, exclude mtime from identity, deduplicate identical IDs, and reject conflicting IDs. Start with contract tests.
