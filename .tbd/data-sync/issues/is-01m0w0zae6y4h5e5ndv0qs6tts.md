---
type: is
id: is-01m0w0zae6y4h5e5ndv0qs6tts
title: Use resumable-sized chunks for GCP dispatch artifact uploads
kind: bug
status: in_progress
priority: 1
version: 3
labels:
  - gcp
dependencies: []
created_at: 2026-08-25T08:35:45.733Z
updated_at: 2026-08-25T17:00:20.440Z
---
A live metaproc gcp run resume packaged a valid 100,301,577-byte workspace. google-cloud-storage's default 100 MiB resumable chunk exceeded the client's 120-second retry deadline on the operator uplink, so dispatch failed before Batch job creation. Set an explicit smaller chunk size and bounded per-request/retry budgets, cover the upload contract, and re-run the exact live dispatch.

## Notes

Implemented bounded resumable upload chunks with explicit request and retry budgets. Focused tests, full verification, and exact-head public CI passed. A live downstream dispatch confirmed job creation; terminal downstream evidence is maintained outside this public repository.
