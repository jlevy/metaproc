---
type: is
id: is-01m0t7zzvad31qstfnwq1hz6xf
title: "PR #35 review 2: guard process-group cleanup against PID reuse"
kind: bug
status: closed
priority: 1
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T15:59:58.826Z
updated_at: 2026-08-24T17:54:41.662Z
closed_at: 2026-08-24T17:54:41.662Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. poll can killpg(handle.pid) after the leader was reaped, with no proof the process group still belongs to the launch. Track create time or observed descendants and refuse cleanup when identity cannot be fenced.
