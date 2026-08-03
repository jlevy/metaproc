---
type: is
id: is-01kz2x8c1fepv4hn5myvcedrgp
title: "PR #8 review MP8-01: repair the stacked PR topology"
kind: bug
status: in_progress
priority: 0
version: 2
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:05.358Z
updated_at: 2026-08-03T04:14:42.488Z
---
PR #8 currently targets main with 60 files despite claiming a 12-file change stacked on codex/company-research-infrastructure. Merge/rebase the current PR #6 head, retarget #8 to that branch, refresh the body and rerun CI. Review: https://github.com/jlevy/metaproc/pull/8#issuecomment-5162195240
