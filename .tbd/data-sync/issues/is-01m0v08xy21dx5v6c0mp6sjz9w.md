---
type: is
id: is-01m0v08xy21dx5v6c0mp6sjz9w
title: "Design: decompose run_process.py along the engine seams"
kind: task
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:17.601Z
updated_at: 2026-08-24T23:04:17.601Z
---
~4,300 lines and growing; the well-factored engine layer below it (item_runner, fan_in, discovery, pathing) produced almost no findings while the monolith produced nearly all of them. After the stack lands, one refactor-only PR extracting: execution context + leaf admission; agent attempt lifecycle; composite/mapped scope evaluation — leaving orchestration + CLI. Do not do it mid-stack. Holistic section 4c.
