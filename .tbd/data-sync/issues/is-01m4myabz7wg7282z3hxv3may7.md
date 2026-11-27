---
type: is
id: is-01m4myabz7wg7282z3hxv3may7
title: "PR #106 A2: scalar report omits quota_reset_at, so the reset-clock pause holds only for mapped steps"
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
created_at: 2026-10-11T07:37:28.292Z
updated_at: 2026-10-11T13:58:44.683Z
started_at: 2026-10-11T13:13:51.803Z
closed_at: 2026-10-11T13:58:44.682Z
close_reason: fixed in 18a1018 (scalar report passes quota_reset_at); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786278
resolution: null
duplicate_of: null
---
Medium. Review A https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352. src/metaproc/commands/run_process.py:3081-3091 _report_scalar_failure passes only elapsed_s; mapped path run_parallel.py:1975-1984 passes quota_reset_at=parse_quota_reset_at(error_str). Fix: pass quota_reset_at for QUOTA_EXHAUSTED on scalar path; scalar test asserting _quota_paused_until set.
