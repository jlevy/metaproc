---
type: is
id: is-01m0tc0msnehs7xyvhpbekkw45
title: Remove obsolete GCP runtime compatibility surfaces
kind: epic
status: closed
priority: 1
version: 6
labels: []
dependencies: []
child_order_hints:
  - is-01m0tc0ygm8r3zh2z98atpkzb8
  - is-01m0tc0ysdxt71nnkvhycc8qsq
  - is-01m0tc0z29tc6qjfskjp6pb4qq
  - is-01m0tc0zayh1avcbtqndky17em
created_at: 2026-08-24T17:10:14.580Z
updated_at: 2026-08-24T18:02:06.919Z
closed_at: 2026-08-24T18:02:06.918Z
close_reason: All child cleanup work is complete and verified. The framework now supports ordinary local execution, full-cloud Batch orchestration, and disposable gcp run without historical gateway or split-state compatibility paths.
resolution: null
duplicate_of: null
---
The current cloud model is local development or disposable GCP Batch, with Filestore scratch and consumer-owned receipt-backed GCS publication. Delete the unregistered archive path, persistent gateway/SSH/tmux paths, and unsupported laptop-orchestrator hybrid behavior instead of maintaining compatibility for historical deployments.
