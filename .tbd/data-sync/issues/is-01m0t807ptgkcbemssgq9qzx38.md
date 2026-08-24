---
type: is
id: is-01m0t807ptgkcbemssgq9qzx38
title: "PR #36 review D3: derive retry policy transport once"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:06.873Z
updated_at: 2026-08-24T16:00:06.873Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. RunExecutionContext carries typed retry policy and independently built raw AuthPoolFlags strings through multiple constructors. Establish one typed source of truth and derive transport at one boundary if this feature is retained.
