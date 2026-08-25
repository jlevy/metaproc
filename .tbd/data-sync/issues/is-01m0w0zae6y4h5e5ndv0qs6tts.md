---
type: is
id: is-01m0w0zae6y4h5e5ndv0qs6tts
title: Use resumable-sized chunks for GCP dispatch artifact uploads
kind: bug
status: in_progress
priority: 1
version: 2
labels:
  - gcp
dependencies: []
created_at: 2026-08-25T08:35:45.733Z
updated_at: 2026-08-25T08:42:25.408Z
---
A live metaproc gcp run resume packaged a valid 100,301,577-byte workspace. google-cloud-storage's default 100 MiB resumable chunk exceeded the client's 120-second retry deadline on the operator uplink, so dispatch failed before Batch job creation. Set an explicit smaller chunk size and bounded per-request/retry budgets, cover the upload contract, and re-run the exact live dispatch.

## Notes

Fixed at 475e5cd with 16 MiB resumable chunks, 120-second request timeout, and 10-minute retry deadline. Focused upload tests: 33 passed; full suite: 4,272 passed, 8 skipped; lint/type/link/public-hygiene/supply-chain green; PR #42 exact-head CI run 32827693773 all five jobs green. Live retry gtia-v2-gcp-stability-tgt-live-resume-20260825-03 uploaded the same ~100.3 MB workspace and created a Batch job; awaiting terminal run evidence before closing.
