---
type: is
id: is-01m0w2fm7y9yzjbz6wz3qvdhws
title: Make RunPool goldens hermetic in clean Linux full-suite CI
kind: bug
status: closed
priority: 0
version: 4
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - testing
  - ci
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T09:02:08.637Z
updated_at: 2026-08-25T09:27:23.036Z
closed_at: 2026-08-25T09:27:23.036Z
close_reason: Fixed through exact PR 37 head d776840; focused tests and full make verify pass, the disposition is published on PR 37, and pinned trading consumer GitHub Actions run 32831285879 is green.
resolution: null
duplicate_of: null
---
The exact ee4ddeb head passes the four goldens alone and the full 4,378-test suite on macOS/Python 3.14, but trading PR #380 clean Ubuntu/Python 3.14 full-suite CI produced four RunPool golden mismatches. Reproduce or isolate the order/platform-dependent state, prove the committed snapshots are not blindly regenerated, add a hermetic regression/fix, run exact CI-equivalent and full verification, then update the pinned trading gitlink.

## Notes

2026-08-25: Root cause isolated to lifecycle goldens inheriting the suite-wide 50 ms pressure poll. Slow Linux full-suite execution crossed the poll and emitted valid adaptive-pressure events; isolated macOS runs completed before it. Commit d776840 sets a 60 s pressure interval only in the lifecycle-golden fixture, retains dedicated controller coverage, passes the four goldens twice and full make verify with 4,378 passed and 8 skipped. Trading PR 380 now pins d776840; close after replacement consumer CI and disposition.
