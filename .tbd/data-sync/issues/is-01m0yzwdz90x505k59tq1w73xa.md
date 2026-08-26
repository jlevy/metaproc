---
type: is
id: is-01m0yzwdz90x505k59tq1w73xa
title: Honor root concurrency across nested executable leaves
kind: bug
status: closed
priority: 0
version: 4
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-26T12:14:25.767Z
updated_at: 2026-08-26T12:25:38.938Z
closed_at: 2026-08-26T12:25:38.937Z
close_reason: Provider-free exact-head execution proved the existing run-owned admission path serializes nested scalar leaves at a root ceiling of one. Added a durable process-event ordering regression; no scheduler change was warranted.
resolution: null
duplicate_of: null
---
A process configured with a single run-wide concurrency slot can still launch sibling agent leaves concurrently when they are reached through mapped or composite scopes. Add a deterministic provider-free regression, identify the ownership break, and route every nested executable leaf through the existing run-owned admission authority. Keep scopes slot-free and do not introduce a second scheduler or controller.

## Notes

Exact-head provider-free integration now runs two sibling scalar agent leaves under a mapped composite with root max_concurrency=1 and an atomic active-process sentinel. The run reports one run-owned pool at max_concurrency=1, completes both submissions, and records no overlap. The suspected capacity violation was a visibility inference: mapped scopes can be structurally running while waiting for leaf admission, and prompt preparation occurs before admission. Actual subprocess launches remain serialized. The strengthened regression preserves this invariant without changing scheduler code.
