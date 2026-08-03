---
type: is
id: is-01kz2x8dghhvw7b51ee0b62xk6
title: "PR #8 review MP8-07: avoid doubled rate-limit taxonomy time"
kind: bug
status: in_progress
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:06.864Z
updated_at: 2026-08-03T04:14:43.808Z
---
src/metaproc/logutil/resource_event_extract.py:339 and src/metaproc/engine/resource_rollup.py:789. Rate-limit duration populates parent and subtype metrics that are summed, doubling taxonomy rollups. Count the umbrella total once and add an exact-duration test.
