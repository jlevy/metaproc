---
type: is
id: is-01m0tcp77xca1zzp25pvq4x43y
title: "PR #35 self-review: stop launch after backend poll failure"
kind: bug
status: closed
priority: 1
version: 4
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T17:22:01.596Z
updated_at: 2026-08-24T17:54:41.726Z
closed_at: 2026-08-24T17:54:41.726Z
close_reason: "Fixed in e233ec1. Per-finding disposition: https://github.com/jlevy/metaproc/pull/35#issuecomment-5399129306. Local make verify passed (4,339 passed, 8 skipped); GitHub CI run 32759134105 passed lint, distribution, and Python 3.12/3.13/3.14."
resolution: null
duplicate_of: null
---
A RunPool backend poll exception is recorded as terminal lane accounting, but the launch is not killed before active ownership and host admission are released. Attempt best-effort backend kill, preserve the original poll error, and test that cleanup is invoked.
