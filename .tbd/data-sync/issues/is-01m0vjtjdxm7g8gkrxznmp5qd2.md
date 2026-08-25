---
type: is
id: is-01m0vjtjdxm7g8gkrxznmp5qd2
title: RunPool activation tests must wait for observed launch state
kind: bug
status: closed
priority: 0
version: 10
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - testing
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-25T04:28:30.013Z
updated_at: 2026-08-25T17:01:15.597Z
closed_at: 2026-08-25T17:01:15.596Z
close_reason: Both activation tests now wait for bounded observed launch state. Repeated focused coverage and full make verify pass; private downstream CI evidence is maintained outside this repository.
resolution: null
duplicate_of: null
---
The full pre-push suite first exposed a fixed-delay assumption in the snapshot test and now exposed the same defect in graceful-shutdown coverage: a submitted process is not guaranteed active within 300 ms under parallel load. Replace each launch-timing sleep with a bounded observed-state wait, keep the child alive long enough to inspect or shut down, and prove repeated focused plus full-suite stability. This is test hardening, not a production retry or admission change.

## Notes

Both fixed-delay activation assumptions were replaced by bounded observed-state waits. Repeated focused coverage and full make verify passed. Private downstream CI evidence is maintained outside this repository.
