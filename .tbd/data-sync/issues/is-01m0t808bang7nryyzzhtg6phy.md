---
type: is
id: is-01m0t808bang7nryyzzhtg6phy
title: "PR #36 review D4: bound retry wait by job lifetime"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:07.530Z
updated_at: 2026-08-24T16:00:07.530Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. auth-retry-max-wait accepts values beyond run or Batch walltime, guaranteeing external termination rather than a controlled result. Cross-validate the bound when a live wait consumer is introduced.
