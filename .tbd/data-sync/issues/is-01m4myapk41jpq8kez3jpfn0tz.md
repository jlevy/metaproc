---
type: is
id: is-01m4myapk41jpq8kez3jpfn0tz
title: "PR #106 A10: suggestion: recognize camel-case quota ids (PerDay, PerMinute)"
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
created_at: 2026-10-11T07:37:39.155Z
updated_at: 2026-10-11T13:58:46.943Z
started_at: 2026-10-11T13:13:54.347Z
closed_at: 2026-10-11T13:58:46.942Z
close_reason: fixed in 4de5816 (perminute/perday tokens); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786278
resolution: null
duplicate_of: null
---
Suggestion. Review A https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352. _RATE_QUOTA_PATTERNS/_HARD_QUOTA_PATTERNS (retry.py:202-224) miss GenerateRequestsPerDayPerProjectPerModel-style ids; probe classified retry.
