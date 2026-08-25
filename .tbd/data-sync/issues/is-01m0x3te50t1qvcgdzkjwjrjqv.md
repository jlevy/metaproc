---
type: is
id: is-01m0x3te50t1qvcgdzkjwjrjqv
title: "R7: measure pooled scalar capacity reservation ordering"
kind: task
status: closed
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T18:44:45.855Z
updated_at: 2026-08-25T19:25:26.728Z
closed_at: 2026-08-25T19:25:26.727Z
close_reason: Explicitly deferred to mp-rrfn smoke measurements; current bounded ordering is accepted until utilization evidence says otherwise.
resolution: null
duplicate_of: null
---
Scalar leaves currently enter the run leaf gate and cross-run host admission, then acquire credentials, before their prepared launch waits on adaptive RunPool capacity. This is bounded and safe for the first slice, but under sustained pressure queued work can reserve host or credential capacity before it launches. Measure it at the 10/32 and concurrent-run smoke gates; change admission ordering only if it causes starvation or material idle capacity.

## Notes

Deferred to measured smoke evidence: current ordering is bounded and safe. mp-rrfn owns M2/M3 and concurrent-run utilization proof before any admission-ordering change.
