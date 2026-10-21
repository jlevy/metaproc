---
type: is
id: is-01m4myb9fsndpap6k37437hfm2
title: "PR #106 B6: scalar quota retries are invisible to the pool retry accounting"
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
created_at: 2026-10-11T07:37:58.520Z
updated_at: 2026-10-11T13:58:48.300Z
started_at: 2026-10-11T13:13:56.337Z
closed_at: 2026-10-11T13:58:48.299Z
close_reason: fixed in c452316 (_wait_scalar_retry_backoff); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786498
resolution: null
duplicate_of: null
---
Low. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. run_process.py:3621-3627 sleeps inline with no record_retry_scheduled/record_retry_consumed (pool.py:1013-1032). Fix: bracket the sleep when scalar_pool is not None, including cancellation.
