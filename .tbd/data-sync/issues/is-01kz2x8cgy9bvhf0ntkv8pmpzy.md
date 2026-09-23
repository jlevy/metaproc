---
type: is
id: is-01kz2x8cgy9bvhf0ntkv8pmpzy
title: "PR #8 review MP8-03: preserve legacy derived IDs on replay"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:05.854Z
updated_at: 2026-08-03T04:27:18.283Z
closed_at: 2026-08-03T04:27:18.283Z
close_reason: "Fixed in PR #8 working tree: exact hashed GCP run selectors with legacy fallback; exact legacy derived-ID replay; validated collision-bounded width controls; centralized/anchored typed partition matching and dash-writer docs. Focused tests 215 passed, full suite passed except the known checkout-basename test, and Python lint/type checks are clean."
---
src/metaproc/ids.py:178 and tests/test_ids.py:54. Legacy underscore parents currently replay to dash child IDs despite exact string identity. Preserve/version historical serialization and test complete historical strings.
