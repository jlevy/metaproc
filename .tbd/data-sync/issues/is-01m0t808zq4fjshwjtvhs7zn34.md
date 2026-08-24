---
type: is
id: is-01m0t808zq4fjshwjtvhs7zn34
title: "PR #36 review D5: unify duration parsing"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d4f3vpf2e815ryxxqkp7
created_at: 2026-08-24T16:00:08.182Z
updated_at: 2026-08-24T16:00:08.182Z
---
Review https://github.com/jlevy/metaproc/pull/36#issuecomment-5397585537. The new strict positive-duration parser and existing permissive parser accept different syntax on one CLI. Reuse one parser and pin rejection behavior.
