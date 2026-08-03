---
type: is
id: is-01kz2y3jccrsgpfe375naa07dg
title: Tie timeout finalization to causal evidence
kind: bug
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-08-03-focused-resource-observability.md
labels:
  - pr-6
  - review
  - landing-blocker
  - pr-10
dependencies:
  - type: blocks
    target: is-01kz3c48tgnj1qvg3z9m18wc4v
parent_id: is-01kz3c2j1me5ng0pkjym3k42rr
created_at: 2026-08-03T04:28:56.587Z
updated_at: 2026-08-03T10:07:21.556Z
closed_at: 2026-08-03T10:07:21.556Z
close_reason: "Implemented and reviewed in Metaproc commit 6dcb522: strict contracts, canonical evidence, usage/meters, ledger projection, immutable budgets, summary/operator views, causal finalization, and inactive local recovery."
---
Define terminal outcome from the exception propagated by the active run path: normal return completed, causal timeout timed_out, cancellation/interrupt cancelled, and other failures failed. Item-level historical timeouts and pool-status/error-string scans must never relabel the run.
