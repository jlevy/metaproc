---
type: is
id: is-01m0tybspk9mmjecddzgxz75ke
title: "Review PR #38: retire gateway and hybrid paths"
kind: task
status: in_progress
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T22:30:54.418Z
updated_at: 2026-08-24T22:31:52.601Z
---
Round-1 review posted 2026-08-24. Blocker: metaproc status <run-id> now returns exit 0 'All items completed' for a nonexistent path (false pass in CI gates). Also: resume breaks on workstation Filestore mount with no remedy in message; SEMANTIC CONFLICT with #37 on _normalize_filestore_runs_path (6 merge-tree conflicts, opposite decisions, same test body edited both ways) — needs intent decision; credential narrowing contradicts its own doc. Positive: batch_client.transport.close() fixes a live main bug, cherry-pick it.
