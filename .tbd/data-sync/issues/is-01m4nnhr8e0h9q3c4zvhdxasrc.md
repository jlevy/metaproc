---
type: is
id: is-01m4nnhr8e0h9q3c4zvhdxasrc
title: "PR #106 C4: CHANGELOG misstates old ordering for failed concurrency_adjust write"
kind: bug
status: closed
priority: 3
version: 3
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m4nnhe30ebaqnt9h1p6m4xxs
hold: null
hold_until: null
created_at: 2026-10-11T14:23:27.501Z
updated_at: 2026-10-11T14:36:29.229Z
started_at: 2026-10-11T14:23:33.687Z
closed_at: 2026-10-11T14:36:29.228Z
close_reason: "Fixed in 647670f: CHANGELOG ordering sentence corrected. Reply https://github.com/jlevy/metaproc/pull/106#issuecomment-6110124542"
resolution: null
duplicate_of: null
---
Low. Review C https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5483637641. CHANGELOG.md:159-162: a failed concurrency_adjust write raised after enforcement, not before.
