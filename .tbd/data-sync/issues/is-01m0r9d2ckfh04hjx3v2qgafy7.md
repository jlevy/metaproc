---
type: is
id: is-01m0r9d2ckfh04hjx3v2qgafy7
title: Prove legacy-run compatibility across durable task facts
kind: task
status: open
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m260z10t7p5h7maqpws2x4rc
parent_id: is-01m0r93gwcj17mn4dmw1ts7fqa
created_at: 2026-08-23T21:46:07.122Z
updated_at: 2026-09-10T18:48:32.447Z
---
Name and test the released run-tree boundary: readers load historical status.yaml, attempt.yaml, and result.yaml when new facts are absent; new runs prefer task facts; mixed or unknown schemas fail visibly instead of silently combining authorities.
