---
type: is
id: is-01m0vjtjdxm7g8gkrxznmp5qd2
title: RunPool activation tests must wait for observed launch state
kind: bug
status: closed
priority: 0
version: 7
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - testing
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-25T04:28:30.013Z
updated_at: 2026-08-25T09:27:23.028Z
closed_at: 2026-08-25T09:27:23.027Z
close_reason: Fixed through exact PR 37 head d776840; focused tests and full make verify pass, the disposition is published on PR 37, and pinned trading consumer GitHub Actions run 32831285879 is green.
resolution: null
duplicate_of: null
---
The full pre-push suite first exposed a fixed-delay assumption in the snapshot test and now exposed the same defect in graceful-shutdown coverage: a submitted process is not guaranteed active within 300 ms under parallel load. Replace each launch-timing sleep with a bounded observed-state wait, keep the child alive long enough to inspect or shut down, and prove repeated focused plus full-suite stability. This is test hardening, not a production retry or admission change.

## Notes

2026-08-25: Both fixed-delay activation assumptions are replaced by bounded observed-state waits through ee4ddeb, which is included in d776840. Repeated focused coverage and full make verify pass at d776840. Keep open until the pinned trading PR 380 replacement CI completes, then close with consumer evidence.
