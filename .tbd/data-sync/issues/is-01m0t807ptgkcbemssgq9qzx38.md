---
type: is
id: is-01m0t807ptgkcbemssgq9qzx38
title: "PR #36 review D3: derive retry policy transport once"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:06.873Z
updated_at: 2026-08-24T19:04:20.709Z
closed_at: 2026-08-24T19:04:20.709Z
close_reason: "Fixed by deleting the unconsumed retry-later flags, defaults, typed/raw dual representation, and duplicate parser from PR #36. Documentation and preflight text were corrected to stop advertising inert behavior. No compatibility-skew transport remains."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. RunExecutionContext carries typed retry policy and independently built raw AuthPoolFlags strings through multiple constructors. Establish one typed source of truth and derive transport at one boundary if this feature is retained.
