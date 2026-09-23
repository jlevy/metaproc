---
type: is
id: is-01m0xsdes7e2qvxhczft52w48w
title: "PR #44 suggestion S2: pin orchestrator secret-ref forwarding invariant"
kind: task
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m0xrncxsdm7q87ywsha5n5x1
created_at: 2026-08-26T01:02:09.190Z
updated_at: 2026-08-26T01:02:09.190Z
---
Deferred non-blocking review suggestion from PR #44. Add a focused assertion that each known hydrated target forwarded by the orchestrator also carries its operator-side secret-ref env name, preventing later registry additions from tripping plaintext refusal only in cloud.
