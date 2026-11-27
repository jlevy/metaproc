---
type: is
id: is-01m4myaj5z3r9tpwyvhywgdgse
title: "PR #106 A7: operator visibility of quota backoff is partial (events formatter, FailureCounts, operator reference)"
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
created_at: 2026-10-11T07:37:34.617Z
updated_at: 2026-10-11T13:58:46.111Z
started_at: 2026-10-11T13:13:53.366Z
closed_at: 2026-10-11T13:58:46.110Z
close_reason: fixed in 87a88b3 for parts a and c (events formatter, operator reference); part b deferred to mp-ivh1; dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786278
resolution: null
duplicate_of: null
---
Low. Review A https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352. (a) commands/pool.py:377-378 prints quota_backoff via raw JSON fallback; (b) FailureCounts lacks quota_exhausted (runpool/status.py:95-103, pool.py:2361-2369); (c) metaproc-operator-reference.md does not mention the quota cut. Fix: formatter branch, counter, one operator-reference sentence.
