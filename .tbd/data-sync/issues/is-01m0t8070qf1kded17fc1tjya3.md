---
type: is
id: is-01m0t8070qf1kded17fc1tjya3
title: "PR #36 review 3B: make pool-recovery waits cancellable"
kind: bug
status: closed
priority: 1
version: 8
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
hold: null
hold_until: null
created_at: 2026-08-24T16:00:06.165Z
updated_at: 2026-08-25T19:29:10.634Z
closed_at: 2026-08-25T19:29:10.633Z
close_reason: The future wait-policy gate mp-l3ot owns cancellability as part of its acceptance criteria; retry-later implementation remains paused.
resolution: duplicate
duplicate_of: is-01m0s7cw0ghtj387wj4nar45we
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. wait_for_pool_recovery uses blocking sleep without a cancellation predicate, which would make the future wait policy uninterruptible for up to six hours. Add cancellation before any consumer is allowed.

## Notes

Deferred from pull request 36 review. The dormant wait helper remains uncancellable. Audit or remove it first; add cancellation only if a verified public behavior requires retention.
