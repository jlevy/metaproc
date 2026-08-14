---
type: is
id: is-01kz2x8c1fepv4hn5myvcedrgp
title: "PR #8 review MP8-01: carve typed IDs directly onto main"
kind: bug
status: closed
priority: 0
version: 7
labels:
  - direct-main
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:05.358Z
updated_at: 2026-08-03T05:10:22.066Z
closed_at: 2026-08-03T05:10:06.101Z
close_reason: "Superseded stacked PR #8 with focused direct-to-main PR #9; full local gate and all GitHub checks passed, and PR #8 was closed with its branch preserved."
---
Rework PR #8 as a focused typed-ID change directly on main. Include only the minimum typed-ID allocation/parsing/run-ID/GCP compatibility prerequisites; exclude PR #6 resource accounting, budget, capture, and finalization code. Verify the isolated diff and CI before retargeting or rewriting the remote branch.

## Notes

Implemented as fresh direct-to-main PR #9 rather than rewriting stacked PR #8. Scope is 15 files: typed ID allocation/parsing/run IDs, exact GCP lookup compatibility, tests/docs, and hook safety. Full local gate passed (3816 passed, 8 skipped); all GitHub checks passed across Python 3.12, 3.13, and 3.14, lint, distribution, and Bugbot.
