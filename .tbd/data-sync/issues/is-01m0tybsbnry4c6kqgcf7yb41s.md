---
type: is
id: is-01m0tybsbnry4c6kqgcf7yb41s
title: "Review PR #37: map composite scopes in-process"
kind: task
status: in_progress
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T22:30:54.068Z
updated_at: 2026-08-24T22:31:52.325Z
---
Phase 2 mapped-composite implementation. Round-1 review posted 2026-08-24. Must-fix before undraft: B1 child scope does not rebind RUN_ID/RUNS_DIR so every mapped item's {{run.dir}} is the parent dir (cross-item contamination, passes validation); B2+B3 non-CLIError abandons siblings (gather has no return_exceptions) + unbounded scope concurrency/FD exhaustion; B6 split graph.py failure-propagation change out (head commit, untested, contradicts shipped docs). Also B4 '..' is a legal item key. Contract items 1/7/8 open.
