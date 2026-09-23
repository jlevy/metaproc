---
type: is
id: is-01m0t808zq4fjshwjtvhs7zn34
title: "PR #36 review D5: unify duration parsing"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:08.182Z
updated_at: 2026-08-24T19:04:20.715Z
closed_at: 2026-08-24T19:04:20.715Z
close_reason: "Fixed by deleting the unconsumed retry-later flags, defaults, typed/raw dual representation, and duplicate parser from PR #36. Documentation and preflight text were corrected to stop advertising inert behavior. No compatibility-skew transport remains."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. The new strict positive-duration parser and existing permissive parser accept different syntax on one CLI. Reuse one parser and pin rejection behavior.
