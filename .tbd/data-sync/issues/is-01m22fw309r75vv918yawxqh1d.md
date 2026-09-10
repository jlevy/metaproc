---
type: is
id: is-01m22fw309r75vv918yawxqh1d
title: Define attempt identity for composite steps
kind: task
status: open
priority: 2
version: 2
labels:
  - design
  - composite
  - resume
dependencies:
  - type: blocks
    target: is-01m0zpdfftvam4ebsvsvq10wve
created_at: 2026-09-09T07:07:16.872Z
updated_at: 2026-09-09T07:08:15.315Z
---
A composite step is the one executable kind with no durable task record of its own. Code (`run_process.py:1549`), agent (`:2513`), mapped-composite items (`:3162`), and manual (`:3863`) all mark a running attempt and a terminal one. A scalar composite marks neither, because there is no settled answer to what an attempt means for a step whose work is a whole child DAG: one evaluation of the child scope, or is the child's own per-step attempt history already the fact? And where would the record live, given that the child scope already owns `<run>/<step>/`?

This is the design question behind `mp-ad60`. `_is_step_completed` reads the per-task record first and falls back to `process-status.yaml`, and `_orchestrate` rewrites every active step in that projection to `pending` before the level loop reaches the completion check. The fallback is load-bearing for fan-out steps, where the projection is authoritative by design. A scalar composite has neither a per-task record nor that exemption, so it re-enters its child orchestrator on every bare resume.

Answering the attempt question resolves `mp-ad60` as a side effect: a composite that marks its own completion never reaches the fallback, and composites gain the attempt history and result records they currently lack. Patching the fallback instead is the worse trade, because the snapshot it would have to read is the stale terminal state the reset exists to suppress, and a wrong reuse predicate skips work rather than merely repeating it.

The `arch-execution-model` backlog already names "persist attempts and task generations as durable facts" as an adoption-path increment; this is a concrete instance of it. Recorded in `docs/project/design/backlog/arch-execution-model-backlog.md`.
