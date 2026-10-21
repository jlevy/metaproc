---
type: is
id: is-01m4mybd25wysz0nba03wt12fe
title: "PR #106 B10: suggestion: clock fixture patches the stdlib monotonic clock process-wide"
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
created_at: 2026-10-11T07:38:02.173Z
updated_at: 2026-10-11T13:58:49.378Z
started_at: 2026-10-11T13:13:57.894Z
closed_at: 2026-10-11T13:58:49.378Z
close_reason: fixed in 055fb5b (pool-module time proxy fixture); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786498
resolution: null
duplicate_of: null
---
Suggestion. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. tests/test_quota_backoff.py:196-200 patches metaproc.runpool.pool.time.monotonic which is time.monotonic for the whole process. Prefer a module-level alias or injected clock.
