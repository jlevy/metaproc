---
type: is
id: is-01m4mybaarwsqry0t86bmpt9dv
title: "PR #106 B7: pre-existing: pool override --clear does not restore capacity when neither ceiling moves"
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
created_at: 2026-10-11T07:37:59.380Z
updated_at: 2026-10-11T13:58:48.567Z
started_at: 2026-10-11T13:13:56.738Z
closed_at: 2026-10-11T13:58:48.566Z
close_reason: fixed in 9d7d8fd (unconditional reconcile after override clear); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786498
resolution: null
duplicate_of: null
---
Low, pre-existing, outside the diff. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. pool.py:2206-2212 returns early from _apply_operator_target once override is gone; _adjust_concurrency only calls _set_capacity when a ceiling changed (pool.py:2309-2310); the quota governor then halves the stale capacity. Fix: reconcile unconditionally in _apply_operator_target.
