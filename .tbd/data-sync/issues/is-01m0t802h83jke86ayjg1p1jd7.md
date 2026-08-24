---
type: is
id: is-01m0t802h83jke86ayjg1p1jd7
title: "PR #35 review N1: move RunPool filter-thread joins off loop"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T16:00:01.576Z
updated_at: 2026-08-24T16:00:01.576Z
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. RunPool joins its log-filter thread synchronously for up to five seconds on the event loop. Move the blocking join off-loop; executor sizing is tracked in PR #33 R4.
