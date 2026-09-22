---
type: is
id: is-01m353af4mtmfm1rp5csvvzdgt
title: Make compact-logs --dry-run render the plan the executor runs
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:41:21.684Z
updated_at: 2026-09-22T17:41:21.684Z
---
`commands/compact_logs.py:55-59` renders `--dry-run` from one traversal and executes from
a different one.

- The dry-run branch calls `plan_compaction(path, ...)`, which walks via
  `_walk_jsonl_files`: `os.walk(followlinks=False)` plus an `is_symlink()` filter.
- The execution branch builds its own list with `path.rglob("*.jsonl")`, which **follows
  directory symlinks and does not skip symlinked files**.

So the executed sweep can compact files the dry run promised it would not touch.

`filesystem-rules`: "Make dry-run render the *same* plan the executor consumes — not a
second implementation that describes what the executor is believed to do. A dry run that
diverges is worse than none, because it is trusted."

Fix: have the command consume `plan_compaction(...).candidates` in both branches.
