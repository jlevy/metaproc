---
type: is
id: is-01kz2x8c1fepv4hn5myvcedrgp
title: "PR #8 review MP8-01: carve typed IDs directly onto main"
kind: bug
status: in_progress
priority: 0
version: 4
labels:
  - direct-main
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:05.358Z
updated_at: 2026-08-03T04:53:50.683Z
---
Rework PR #8 as a focused typed-ID change directly on main. Include only the minimum typed-ID allocation/parsing/run-ID/GCP compatibility prerequisites; exclude PR #6 resource accounting, budget, capture, and finalization code. Verify the isolated diff and CI before retargeting or rewriting the remote branch.

## Notes

Direct-main prototype completed on local branch codex/typed-id-direct-main: 14 files, +742/-46, no PR6 resource/budget/capture/finalizer dependencies. 155 focused tests passed; static gate green; 3805 non-layout tests passed before one stale-bytecode path failure, which was isolated and passed after cache refresh; 10 disk-dependent layout tests pass separately with the host disk threshold disabled. Remote PR has not been rewritten or retargeted.
