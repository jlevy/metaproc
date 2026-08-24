---
type: is
id: is-01m0rm07zrfcv9jagtvd67bkch
title: Reuse the GCP run logging client and show Batch state transitions
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
created_at: 2026-08-24T00:51:21.195Z
updated_at: 2026-08-24T00:58:59.447Z
closed_at: 2026-08-24T00:58:59.447Z
close_reason: Fixed in 18d78d6; live GCP probe validated client reuse/state reporting and full local plus PR CI passed.
resolution: null
duplicate_of: null
---
A live generic GCP image probe showed tail_gcp_run_logs opening a new Cloud Logging client every two-second poll, leaking file descriptors and flooding stderr with gRPC fork warnings. Reuse and close one client for the monitor lifetime and print state transitions so silent provisioning waits are observable.
