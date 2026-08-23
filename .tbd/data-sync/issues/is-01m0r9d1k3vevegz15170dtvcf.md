---
type: is
id: is-01m0r9d1k3vevegz15170dtvcf
title: Persist task generations and fence-aware commit manifests
kind: feature
status: open
priority: 1
version: 3
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r9d1zvx40602kj7egnnqas
  - type: blocks
    target: is-01m0r9d2ckfh04hjx3v2qgafy7
parent_id: is-01m0r93gwcj17mn4dmw1ts7fqa
created_at: 2026-08-23T21:46:06.307Z
updated_at: 2026-08-23T21:46:17.306Z
---
Add typed task-generation state and a create-only validated commit manifest. Accept a commit only for the current generation and fence epoch, record superseded endings as history only, and project legacy result/status records from accepted facts while existing runs remain readable.
