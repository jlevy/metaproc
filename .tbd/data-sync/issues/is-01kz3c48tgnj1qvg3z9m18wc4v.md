---
type: is
id: is-01kz3c48tgnj1qvg3z9m18wc4v
title: Finalize and recover resources from local ledger evidence
kind: feature
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-03-focused-resource-observability.md
labels:
  - resources
  - pr-10
dependencies:
  - type: blocks
    target: is-01kz3c4emrbq58fss2nths2dnd
parent_id: is-01kz3c2j1me5ng0pkjym3k42rr
created_at: 2026-08-03T08:33:59.632Z
updated_at: 2026-08-03T08:34:05.591Z
---
Run idempotent resource finalization before lease release on success, failure, timeout, and cancellation while preserving the original exception. Teach status to repair missing/stale artifacts only for inactive runs using the immutable snapshot and local event/source files; rebuild totals rather than reuse cached resources.json and perform no provider calls.
