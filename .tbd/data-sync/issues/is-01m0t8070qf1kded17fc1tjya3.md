---
type: is
id: is-01m0t8070qf1kded17fc1tjya3
title: "PR #36 review 3B: make pool-recovery waits cancellable"
kind: bug
status: open
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
hold: paused
created_at: 2026-08-24T16:00:06.165Z
updated_at: 2026-08-24T19:04:22.155Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. wait_for_pool_recovery uses blocking sleep without a cancellation predicate, which would make the future wait policy uninterruptible for up to six hours. Add cancellation before any consumer is allowed.

## Notes

Deferred from PR #36 review 3B. The dormant wait helper remains uncancellable. Audit/remove it first; add cancellation only if a verified consumer or v3.0-pre smoke earns retention.
