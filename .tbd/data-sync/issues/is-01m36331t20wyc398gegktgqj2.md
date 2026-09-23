---
type: is
id: is-01m36331t20wyc398gegktgqj2
title: Attach launch context and effective input bindings to each task attempt
kind: feature
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-09-23T02:56:33.089Z
updated_at: 2026-09-23T02:56:33.089Z
---
Execution evidence per attempt: today TaskAttemptRecord (src/metaproc/models/runtime.py) preserves lifecycle facts and the attempt/result records carry a step hash, but no reference to the launch (run-config revision) that dispatched the attempt or the effective input bindings and scope bindings in force, and run-config.yaml is rewritten on each resume. Target (src/metaproc/docs/execution-model-design.md, 'Inputs: Identity, Reuse, and Evidence'): each launch/resume gets an immutable context record; each attempt references the context actually dispatched to it and its effective bindings; each commit references its producing attempt and the upstream commits it consumed; capture worker-side evidence where execution occurs. Mixing versions inside one run stays legitimate and old output is never relabeled with the latest config. Origin: the second broader gap named in the review of PR #96 (pre-existing on main).
