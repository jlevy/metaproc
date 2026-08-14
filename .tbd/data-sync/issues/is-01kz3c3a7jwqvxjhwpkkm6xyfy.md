---
type: is
id: is-01kz3c3a7jwqvxjhwpkkm6xyfy
title: Normalize agent usage, list cost, and exact provider meters
kind: feature
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/done/plan-2026-08-03-focused-resource-observability.md
labels:
  - resources
  - pr-10
dependencies:
  - type: blocks
    target: is-01kz3c3f3kfqmg2gp8j080whvz
parent_id: is-01kz3c2j1me5ng0pkjym3k42rr
created_at: 2026-08-03T08:33:28.305Z
updated_at: 2026-08-09T18:56:51.076Z
closed_at: 2026-08-03T10:07:21.530Z
close_reason: "Implemented and reviewed in Metaproc commit 6dcb522: strict contracts, canonical evidence, usage/meters, ledger projection, immutable budgets, summary/operator views, causal finalization, and inactive local recovery."
---
Unify whole-attempt usage for resource and trace consumers; fix Codex cached-input pricing, Claude nested modelUsage, Gemini disjoint token buckets, measured zero, and Pi estimated cost. Emit exact provider/product/meter/unit observations where proven and explicit unmeasured gaps otherwise; never infer API requests from turns or steps.
