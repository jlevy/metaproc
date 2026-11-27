---
type: is
id: is-01m4mybb4e7zrpjm3rk7rfcgdt
title: "PR #106 B8: RetryPolicy docstring still says quota is never retried"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m4my9r60j9s9nkm8t1rnam6f
hold: null
hold_until: null
created_at: 2026-10-11T07:38:00.205Z
updated_at: 2026-10-11T13:58:48.838Z
started_at: 2026-10-11T13:13:57.075Z
closed_at: 2026-10-11T13:58:48.837Z
close_reason: fixed in b1e21d7 (RetryPolicy docstring); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786498
resolution: null
duplicate_of: null
---
Low. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. src/metaproc/models/authored.py:400-401. Fix: name hard quota caps (daily, monthly, billing).
