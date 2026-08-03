---
type: is
id: is-01kz2y3hdgz6zveescccx821xh
title: Make tool-call identity and totals budget-safe
kind: bug
status: open
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
    target: is-01kz3c3a7jwqvxjhwpkkm6xyfy
  - type: blocks
    target: is-01kz3c3f3kfqmg2gp8j080whvz
parent_id: is-01kz3c2j1me5ng0pkjym3k42rr
created_at: 2026-08-03T04:28:55.593Z
updated_at: 2026-08-03T08:33:33.299Z
---
Preserve producer invocation IDs across Claude, Gemini, Pi, and Codex; pair starts/results by ID; use stable source-local ordinal fallback; and reconcile terminal aggregate counts as residual only. Make tool_calls/tool_failures trustworthy for rollups and budgets with focused red-green tests.
