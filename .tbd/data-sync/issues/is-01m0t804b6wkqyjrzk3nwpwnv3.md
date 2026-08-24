---
type: is
id: is-01m0t804b6wkqyjrzk3nwpwnv3
title: "PR #36 review 1: omit default retry-later transport"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:03.429Z
updated_at: 2026-08-24T19:04:20.685Z
closed_at: 2026-08-24T19:04:20.685Z
close_reason: "Fixed by deleting the unconsumed retry-later flags, defaults, typed/raw dual representation, and duplicate parser from PR #36. Documentation and preflight text were corrected to stop advertising inert behavior. No compatibility-skew transport remains."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. Non-empty CLI defaults always serialize retry policy, causing perpetual resume diffs and breaking older worker CLIs. Use empty transport defaults mapped to canonical internal defaults and prove baseline dispatch emits no env vars or flags.
