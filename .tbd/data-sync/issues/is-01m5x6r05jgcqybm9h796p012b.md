---
type: is
id: is-01m5x6r05jgcqybm9h796p012b
title: "PR 117 A5/A6: correct the kill --force prose and the PR description"
kind: task
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-10-26T22:54:20.850Z
updated_at: 2026-10-26T22:54:20.850Z
---
Senior review A, Low findings.

A5: the operator reference claimed --force sends SIGKILL and writes no summaries. On the pool path kill_pool(force=True) SIGKILLs the selected worker children but still SIGTERMs the pool process, which is the orchestrator (runpool/status.py records pid=os.getpid()), so signal finalization does run. Only the no-pool orchestrator-lease path SIGKILLs the parent. Prose corrected in metaproc-operator-reference.md; kill behavior unchanged.

A6: the PR body described a 10 s finish bound, a 13 s kill wait, SIGTERM-only handling and blocked CI. Rewritten around the final implementation.
