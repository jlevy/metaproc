---
type: is
id: is-01m4myaecz3jet0zd9erkyz98v
title: "PR #106 A4: launch time is reconstructed at report time, not at exit"
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
created_at: 2026-10-11T07:37:30.782Z
updated_at: 2026-10-11T13:58:45.244Z
started_at: 2026-10-11T13:13:52.443Z
closed_at: 2026-10-11T13:58:45.243Z
close_reason: fixed in eca6447 (pool-side ProcessResult.admitted_at stamp); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786278
resolution: null
duplicate_of: null
---
Low. Review A https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352. pool.py:972-977 infers launch as now - elapsed_s inside record_failure_class; report happens after extract_log_error, _complete_auth_attempt, try_compact_log, and the threadsafe hop. Fix: stamp launch pool-side or capture monotonic - elapsed_s at result receipt.
