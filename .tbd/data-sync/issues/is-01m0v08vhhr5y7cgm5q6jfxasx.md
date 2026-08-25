---
type: is
id: is-01m0v08vhhr5y7cgm5q6jfxasx
title: "PR #37 I7: split the graph.py failure-propagation change into its own PR"
kind: task
status: open
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:15.152Z
updated_at: 2026-08-24T23:14:29.895Z
---
The head commit changes _requires_only_finished semantics globally (collect: never feeds needs, so at least two unrelated shapes change blocking behavior), with one test, contradicting shipped arch-metaproc-core.md, no CHANGELOG, and no CI at that head. Land it separately with the missing shape tests and the doc correction. Review: pull/37 comment (B6); holistic ledger #7.

## Notes

Re-verified OPEN at #37 head 49064f0: commit 0995cdd (graph.py diamond/failure-propagation change) still in the PR range.
