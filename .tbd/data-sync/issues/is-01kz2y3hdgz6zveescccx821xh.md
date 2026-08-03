---
type: is
id: is-01kz2y3hdgz6zveescccx821xh
title: Make tool-call identity and totals budget-safe
kind: bug
status: open
priority: 1
version: 2
labels:
  - pr-6
  - review
  - landing-blocker
  - deferred
  - follow-up
dependencies: []
parent_id: is-01kz2xyqqrkherk08h96kw58k9
created_at: 2026-08-03T04:28:55.593Z
updated_at: 2026-08-03T04:30:33.318Z
---
Resolve MP6-4: preserve provider invocation identity, pair starts/results correctly, avoid terminal-plus-span double counting, and make tool_calls/tool_failures trustworthy for budgets. Prefer the smallest canonical attempt-level normalization that removes incorrect totals.
