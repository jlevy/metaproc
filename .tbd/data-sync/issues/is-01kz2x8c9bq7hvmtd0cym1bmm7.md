---
type: is
id: is-01kz2x8c9bq7hvmtd0cym1bmm7
title: "PR #8 review MP8-02: preserve exact run identity in GCP selectors"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:05.610Z
updated_at: 2026-08-03T04:27:18.258Z
closed_at: 2026-08-03T04:27:18.258Z
close_reason: "Fixed in PR #8 working tree: exact hashed GCP run selectors with legacy fallback; exact legacy derived-ID replay; validated collision-bounded width controls; centralized/anchored typed partition matching and dash-writer docs. Focused tests 215 passed, full suite passed except the known checkout-basename test, and Python lint/type checks are clean."
---
src/metaproc/cloud/gcp/batch_backend.py:591 and src/metaproc/commands/gcp.py:215. Lossy GCP label sanitization collapses distinct underscore, dash, and dot IDs. Add an exact identity locator with legacy fallback and collision tests. Review thread: https://github.com/jlevy/metaproc/pull/8#discussion_r3701147031
