---
type: is
id: is-01kz2x8c9bq7hvmtd0cym1bmm7
title: "PR #8 review MP8-02: preserve exact run identity in GCP selectors"
kind: bug
status: in_progress
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:05.610Z
updated_at: 2026-08-03T04:14:42.699Z
---
src/metaproc/cloud/gcp/batch_backend.py:591 and src/metaproc/commands/gcp.py:215. Lossy GCP label sanitization collapses distinct underscore, dash, and dot IDs. Add an exact identity locator with legacy fallback and collision tests. Review thread: https://github.com/jlevy/metaproc/pull/8#discussion_r3701147031
