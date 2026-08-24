---
type: is
id: is-01m0t808bang7nryyzzhtg6phy
title: "PR #36 review D4: bound retry wait by job lifetime"
kind: bug
status: open
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0s0r624c0eszrgnq4qgjjbe
hold: paused
created_at: 2026-08-24T16:00:07.530Z
updated_at: 2026-08-24T19:04:22.163Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. auth-retry-max-wait accepts values beyond run or Batch walltime, guaranteeing external termination rather than a controlled result. Cross-validate the bound when a live wait consumer is introduced.

## Notes

Deferred from PR #36 review D4. There is no public max-wait surface now. Cross-bound wait limits with run and Batch lifetime only if the smoke-driven audit retains a live wait policy.
