---
type: is
id: is-01kz2xyz13yt7mdhcraznw9w9q
title: Fix legacy budget cost-field semantics
kind: bug
status: closed
priority: 1
version: 3
labels:
  - pr-6
  - review
dependencies: []
parent_id: is-01kz2xyqqrkherk08h96kw58k9
created_at: 2026-08-03T04:26:25.699Z
updated_at: 2026-08-03T04:30:36.705Z
closed_at: 2026-08-03T04:30:36.704Z
close_reason: null
---
Legacy max_budget_usd must not be evaluated against a cost field whose semantic basis may be provider list cost rather than actual spend. Preserve backward compatibility while making the budget basis explicit and correct.
