---
type: is
id: is-01m4myb61x4egvdbkstf3y7dqp
title: "PR #106 B2: quota cut does not survive a resume when an operator cap is in saved controller state"
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
created_at: 2026-10-11T07:37:54.991Z
updated_at: 2026-10-11T13:58:47.488Z
started_at: 2026-10-11T13:13:55.045Z
closed_at: 2026-10-11T13:58:47.487Z
close_reason: fixed in f020632 (ControllerStatus.quota_hold_remaining_s in resume rule); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786498
resolution: null
duplicate_of: null
---
Low. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. pool.py:733-748 floors restored provider_ceiling to fresh estimate when operator_cap < max and recent_rate_limits == 0 and pending_retries == 0; a quota cut leaves neither signal. Fix: persist a quota marker in ControllerStatus and treat as live pressure, or document the limitation.
