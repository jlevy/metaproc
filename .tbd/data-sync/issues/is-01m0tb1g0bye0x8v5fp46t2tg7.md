---
type: is
id: is-01m0tb1g0bye0x8v5fp46t2tg7
title: Close the generated Batch transport after GCP log tailing
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
created_at: 2026-08-24T16:53:13.867Z
updated_at: 2026-08-24T17:02:40.587Z
closed_at: 2026-08-24T17:02:40.578Z
close_reason: "Fixed in 50ef10b; focused tests, full make verify, and Metaproc PR #38 CI all pass."
resolution: null
duplicate_of: null
---
A successful blocking GCP run raises AttributeError because google-cloud-batch 0.21.0 BatchServiceClient has no close() method. Close its generated transport instead, add regression coverage, and verify the cloud wrapper exits with the Batch job's terminal status.
