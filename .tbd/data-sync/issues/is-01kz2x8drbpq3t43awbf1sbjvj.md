---
type: is
id: is-01kz2x8drbpq3t43awbf1sbjvj
title: "PR #8 review MP8-08: make typed ID width controls round-trip"
kind: bug
status: in_progress
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:07.115Z
updated_at: 2026-08-03T04:14:44.033Z
---
src/metaproc/ids.py:36 and docs/conventions.md:90. Non-default bits/length outputs do not consistently round-trip and invalid lengths are not validated. Define safe bounds, make compact readers width-compatible, expose consistent derived controls, and test non-default widths.
