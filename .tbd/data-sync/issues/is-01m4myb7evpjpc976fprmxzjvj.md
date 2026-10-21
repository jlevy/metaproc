---
type: is
id: is-01m4myb7evpjpc976fprmxzjvj
title: "PR #106 B4: action cut is logged when nothing was cut (floor)"
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
created_at: 2026-10-11T07:37:56.439Z
updated_at: 2026-10-11T13:58:48.030Z
started_at: 2026-10-11T13:13:55.712Z
closed_at: 2026-10-11T13:58:48.029Z
close_reason: fixed in 19d6e7b (action follows outcome); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786498
resolution: null
duplicate_of: null
---
Low. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. pool.py:977 decides cut before the min_concurrency floor (:985-988); action, warning, _quota_cut_at follow the decision not the outcome. Fix: action from outcome; advance _quota_cut_at only on a real cut.
