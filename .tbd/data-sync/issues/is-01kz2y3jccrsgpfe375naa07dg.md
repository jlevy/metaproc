---
type: is
id: is-01kz2y3jccrsgpfe375naa07dg
title: Tie timeout finalization to causal evidence
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
created_at: 2026-08-03T04:28:56.587Z
updated_at: 2026-08-03T04:30:33.339Z
---
Resolve B4: terminal timed_out state must follow evidence from the failing execution path, not a latest-pool or run-wide historical heuristic.
