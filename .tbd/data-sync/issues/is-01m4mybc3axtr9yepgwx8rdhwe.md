---
type: is
id: is-01m4mybc3axtr9yepgwx8rdhwe
title: "PR #106 B9: suggestion: shrink scalar report skew; stamp admission not launch"
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
created_at: 2026-10-11T07:38:01.192Z
updated_at: 2026-10-11T13:58:49.108Z
started_at: 2026-10-11T13:13:57.493Z
closed_at: 2026-10-11T13:58:49.107Z
close_reason: fixed in eca6447 (admission stamped at semaphore acquisition); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786498
resolution: null
duplicate_of: null
---
Suggestion, refines A4. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. Move classify_failure/_report_scalar_failure to just after run_process.py:3534; if a stamp is adopted, stamp semaphore acquisition (pool.py:1369) rather than active.start_time (:1440).
