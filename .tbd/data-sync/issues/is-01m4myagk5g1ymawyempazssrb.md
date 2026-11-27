---
type: is
id: is-01m4myagk5g1ymawyempazssrb
title: "PR #106 A6: mapped code path blocks its sequential loop for 60 s per quota retry"
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
created_at: 2026-10-11T07:37:33.025Z
updated_at: 2026-10-11T13:58:45.802Z
started_at: 2026-10-11T13:13:53.058Z
closed_at: 2026-10-11T13:58:45.801Z
close_reason: fixed in 68c313b (mapped code path keeps compute_backoff); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786278
resolution: null
duplicate_of: null
---
Low. Review A https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352. run_parallel.py:1012-1017 uses retry_backoff_s then time.sleep inside the sequential batch loop (:906). Fix: keep compute_backoff on code path, or document the stall in metaproc-design.md.
