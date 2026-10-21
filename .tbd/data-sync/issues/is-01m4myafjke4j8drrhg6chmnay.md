---
type: is
id: is-01m4myafjke4j8drrhg6chmnay
title: "PR #106 A5: elapsed_s None cuts the ceiling though None now means nothing launched"
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
created_at: 2026-10-11T07:37:31.986Z
updated_at: 2026-10-11T13:58:45.518Z
started_at: 2026-10-11T13:13:52.755Z
closed_at: 2026-10-11T13:58:45.517Z
close_reason: fixed in 2b0eeb3 (unknown admission only holds); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786278
resolution: null
duplicate_of: null
---
Low. Review A https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352. pool.py:974-975 cuts on unknown launch time when no hold is active; None comes only from pre-launch failures (run_parallel.py:1869, :2198), reachable e.g. via PoolSlotUnavailableError excluded= label names containing quota. Fix: treat None as hold-only; update test_one_quota_failure_halves_the_provider_ceiling to pass elapsed_s.
