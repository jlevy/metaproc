---
type: is
id: is-01m0t804b6wkqyjrzk3nwpwnv3
title: "PR #36 review 1: omit default retry-later transport"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:03.429Z
updated_at: 2026-08-24T16:00:03.429Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. Non-empty CLI defaults always serialize retry policy, causing perpetual resume diffs and breaking older worker CLIs. Use empty transport defaults mapped to canonical internal defaults and prove baseline dispatch emits no env vars or flags.
