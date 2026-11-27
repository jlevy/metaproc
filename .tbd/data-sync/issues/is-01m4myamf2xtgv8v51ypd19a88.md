---
type: is
id: is-01m4myamf2xtgv8v51ypd19a88
title: "PR #106 A9: suggestion: feed the governor from the exit-code rescue paths"
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
created_at: 2026-10-11T07:37:36.993Z
updated_at: 2026-10-11T13:58:46.668Z
started_at: 2026-10-11T13:13:53.980Z
closed_at: 2026-10-11T13:58:46.668Z
close_reason: "declined: scalar rescue exit_error carries no log text (classifies crash), mapped rescue reporting is out of scope; dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786278"
resolution: null
duplicate_of: null
---
Suggestion. Review A https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352. run_process.py:3558-3593 and run_parallel.py:2257-2259 accept a non-zero exit whose outputs validate without classifying, so the refusal never reaches the governor.
