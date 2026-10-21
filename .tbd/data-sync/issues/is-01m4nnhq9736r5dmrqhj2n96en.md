---
type: is
id: is-01m4nnhq9736r5dmrqhj2n96en
title: "PR #106 C1: unguarded retry_scheduled and quota_pause_started writes in the scalar attempt loop; record_retry_scheduled leaks a pending retry"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m4nnhe30ebaqnt9h1p6m4xxs
hold: null
hold_until: null
created_at: 2026-10-11T14:23:26.503Z
updated_at: 2026-10-11T14:36:28.657Z
started_at: 2026-10-11T14:23:33.396Z
closed_at: 2026-10-11T14:36:28.643Z
close_reason: Fixed in 647670f (guarded retry_scheduled and quota pause writes; counter after write); six tests failed first. Reply https://github.com/jlevy/metaproc/pull/106#issuecomment-6110124542
resolution: null
duplicate_of: null
---
Low. Review C https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5483637641. pool.py:1083-1085 record_retry_scheduled increments _pending_retries before an unguarded EventLogger write; pool.py:870-881 pause_for_quota writes quota_pause_started unguarded. Both now reachable from the scalar loop (run_process.py:3117-3118 _wait_scalar_retry_backoff, _report_scalar_failure). A failing sink fails the step and leaves a phantom pending retry.
