---
type: is
id: is-01m0t7zzvad31qstfnwq1hz6xf
title: "PR #35 review 2: guard process-group cleanup against PID reuse"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T15:59:58.826Z
updated_at: 2026-08-24T15:59:58.826Z
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. poll can killpg(handle.pid) after the leader was reaped, with no proof the process group still belongs to the launch. Track create time or observed descendants and refuse cleanup when identity cannot be fenced.
