---
type: is
id: is-01kz2x8drbpq3t43awbf1sbjvj
title: "PR #8 review MP8-08: make typed ID width controls round-trip"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:07.115Z
updated_at: 2026-08-03T04:27:18.296Z
closed_at: 2026-08-03T04:27:18.296Z
close_reason: "Fixed in PR #8 working tree: exact hashed GCP run selectors with legacy fallback; exact legacy derived-ID replay; validated collision-bounded width controls; centralized/anchored typed partition matching and dash-writer docs. Focused tests 215 passed, full suite passed except the known checkout-basename test, and Python lint/type checks are clean."
---
src/metaproc/ids.py:36 and docs/conventions.md:90. Non-default bits/length outputs do not consistently round-trip and invalid lengths are not validated. Define safe bounds, make compact readers width-compatible, expose consistent derived controls, and test non-default widths.
