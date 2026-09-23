---
type: is
id: is-01kz3c48tgnj1qvg3z9m18wc4v
title: Finalize and recover resources from local ledger evidence
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
    target: is-01kz3c4emrbq58fss2nths2dnd
parent_id: is-01kz3c2j1me5ng0pkjym3k42rr
created_at: 2026-08-03T08:33:59.632Z
updated_at: 2026-08-09T18:56:52.084Z
closed_at: 2026-08-03T10:07:21.562Z
close_reason: "Implemented and reviewed in Metaproc commit 6dcb522: strict contracts, canonical evidence, usage/meters, ledger projection, immutable budgets, summary/operator views, causal finalization, and inactive local recovery."
---
Run idempotent resource finalization before lease release on success, failure, timeout, and cancellation while preserving the original exception. Teach status to repair missing/stale artifacts only for inactive runs using the immutable snapshot and local event/source files; rebuild totals rather than reuse cached resources.json and perform no provider calls.
