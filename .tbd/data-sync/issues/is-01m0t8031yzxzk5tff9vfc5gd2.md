---
type: is
id: is-01m0t8031yzxzk5tff9vfc5gd2
title: "PR #35 review D1: document LocalBackend lifecycle rewrite"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T16:00:02.109Z
updated_at: 2026-08-24T17:54:41.707Z
closed_at: 2026-08-24T17:54:41.707Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. CHANGELOG and architecture docs omit the widest-blast-radius change: poll/kill now sweep process groups, can block, and can fail. Document the final contract after findings 2 and 3 settle.
