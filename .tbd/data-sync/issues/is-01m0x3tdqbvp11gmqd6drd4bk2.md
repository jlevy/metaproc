---
type: is
id: is-01m0x3tdqbvp11gmqd6drd4bk2
title: "R6: document the same-upstream mixed dependency-clause limitation"
kind: task
status: closed
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T18:44:45.418Z
updated_at: 2026-08-25T19:25:26.391Z
closed_at: 2026-08-25T19:25:26.389Z
close_reason: "Explicitly deferred: no current process requires same-upstream clause distinction; limitation is documented and model expansion remains evidence-triggered."
resolution: null
duplicate_of: null
---
Failure propagation now preserves a strict affected dependency beside a tolerant finished collection when they resolve to distinct direct dependencies. ResolvedStep.needs collapses two authored clauses naming the same upstream, so that rarer mixed contract is not edge-distinguishable without widening the plan model. State this limit explicitly and defer model expansion until a real process requires it.

## Notes

Deferred by design: the current resolved plan collapses multiple clauses naming one upstream. No current process requires widening that contract; the limitation is explicit in the plan.
