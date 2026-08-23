---
type: is
id: is-01m0r9d2ckfh04hjx3v2qgafy7
title: Prove legacy-run compatibility across durable task facts
kind: task
status: open
priority: 1
version: 1
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies: []
parent_id: is-01m0r93gwcj17mn4dmw1ts7fqa
created_at: 2026-08-23T21:46:07.122Z
updated_at: 2026-08-23T21:46:07.122Z
---
Name and test the released run-tree boundary: readers load historical status.yaml, attempt.yaml, and result.yaml when new facts are absent; new runs prefer task facts; mixed or unknown schemas fail visibly instead of silently combining authorities.
