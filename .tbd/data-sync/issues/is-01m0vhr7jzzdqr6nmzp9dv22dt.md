---
type: is
id: is-01m0vhr7jzzdqr6nmzp9dv22dt
title: "PR #35 N5: do not let cancelled status poison later partial runs"
kind: bug
status: in_progress
priority: 1
version: 3
labels: []
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0v08wy0cem0nwa7zeejr8qd
created_at: 2026-08-25T04:09:44.798Z
updated_at: 2026-08-25T05:51:53.752Z
---
Correct process-status precedence and resume behavior so a prior cancelled state does not outrank new running, failed, or completed work in a later partial run. Add a cancel-then-partial-resume regression.
