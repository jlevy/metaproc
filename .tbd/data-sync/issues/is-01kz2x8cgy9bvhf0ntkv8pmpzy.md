---
type: is
id: is-01kz2x8cgy9bvhf0ntkv8pmpzy
title: "PR #8 review MP8-03: preserve legacy derived IDs on replay"
kind: bug
status: in_progress
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:05.854Z
updated_at: 2026-08-03T04:14:42.920Z
---
src/metaproc/ids.py:178 and tests/test_ids.py:54. Legacy underscore parents currently replay to dash child IDs despite exact string identity. Preserve/version historical serialization and test complete historical strings.
