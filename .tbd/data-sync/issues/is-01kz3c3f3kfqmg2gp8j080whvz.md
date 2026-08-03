---
type: is
id: is-01kz3c3f3kfqmg2gp8j080whvz
title: Project all resource artifacts from the reconciled ledger
kind: feature
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-08-03-focused-resource-observability.md
labels:
  - resources
  - pr-10
dependencies:
  - type: blocks
    target: is-01kz2y3hxx4817s9sny6qr0629
  - type: blocks
    target: is-01kz3c3vadwxpw6p07rt1t7dbp
  - type: blocks
    target: is-01kz3c48tgnj1qvg3z9m18wc4v
parent_id: is-01kz3c2j1me5ng0pkjym3k42rr
created_at: 2026-08-03T08:33:33.299Z
updated_at: 2026-08-03T10:07:21.535Z
closed_at: 2026-08-03T10:07:21.535Z
close_reason: "Implemented and reviewed in Metaproc commit 6dcb522: strict contracts, canonical evidence, usage/meters, ledger projection, immutable budgets, summary/operator views, causal finalization, and inactive local recovery."
---
Make reconciled ResourceEvents the sole input to hierarchy, taxonomy, scalar, and meter totals. Count unresolved ownership once, materialize missing item/tool leaves deterministically, keep actual/list cost separate, retain strict V1 reads, and expose one projector reusable by normal finalization and recovery.
