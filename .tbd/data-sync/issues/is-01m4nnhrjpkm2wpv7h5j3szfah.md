---
type: is
id: is-01m4nnhrjpkm2wpv7h5j3szfah
title: "PR #106 C5: suggestion: surface quota_hold_remaining_s in metaproc stats"
kind: task
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m4nnhe30ebaqnt9h1p6m4xxs
created_at: 2026-10-11T14:23:27.830Z
updated_at: 2026-10-11T14:36:30.108Z
closed_at: 2026-10-11T14:36:30.108Z
close_reason: Deferred to open mp-ivh1 (retitled to cover stats quota hold). Reply https://github.com/jlevy/metaproc/pull/106#issuecomment-6110124542
resolution: duplicate
duplicate_of: is-01m4nkmcgzq6sf5vqzk8c7kpj7
---
Suggestion. Review C https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5483637641. stats/engine.py:339-418 drops quota_hold_remaining_s; add to stats model and formatters.py:198-199.
